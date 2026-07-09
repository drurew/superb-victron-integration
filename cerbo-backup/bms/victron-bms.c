/*
 * victron-bms.c — Compiled BMS driver for SuperB Epsilon V2 → Victron Cerbo GX
 *
 * Zero dependencies beyond libc. Uses raw SocketCAN and raw D-Bus wire protocol.
 * Compile: arm-linux-gnu-gcc -Os -s -o victron-bms victron-bms.c
 * Deploy:  scp victron-bms root@cerbo:/data/bms/
 * Run:     /data/bms/victron-bms vecan0
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <math.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <net/if.h>
#include <fcntl.h>
#include <poll.h>

/* ─── SocketCAN definitions (from <linux/can.h> + <linux/can/raw.h>) ─── */

#ifndef AF_CAN
#define AF_CAN 29
#endif
#ifndef PF_CAN
#define PF_CAN AF_CAN
#endif
#ifndef CAN_RAW
#define CAN_RAW 1
#endif

typedef unsigned int canid_t;

struct can_frame {
    canid_t can_id;
    unsigned char can_dlc;
    unsigned char __pad;
    unsigned short __res0;
    unsigned char data[8] __attribute__((aligned(8)));
};

/* ─── D-Bus wire protocol constants ──────────────────────────────────── */

#define DBUS_SYSTEM_BUS_PATH "/var/run/dbus/system_bus_socket"
#define DBUS_MESSAGE_TYPE_METHOD_CALL  1
#define DBUS_MESSAGE_TYPE_METHOD_RETURN 2
#define DBUS_MESSAGE_TYPE_SIGNAL       4
#define DBUS_HEADER_FLAG_NO_REPLY_EXPECTED 0x01
#define DBUS_HEADER_FLAG_NO_AUTO_START      0x02

/* ─── SDO parameter definitions ──────────────────────────────────────── */

typedef struct {
    unsigned short index;
    unsigned char  subindex;
    char           dtype;   /* 'i'=int32, 'I'=uint32, 'h'=int16, 'H'=uint16, 'B'=uint8 */
    double         divisor;
} sdo_param;

static const sdo_param sdo_params[] = {
    /* name                   index   sub  dtype  divisor */
    [0]  /* voltage        */ {0x6060, 0x00, 'i', 1024.0},
    [1]  /* current        */ {0x2010, 0x00, 'i', 1000.0},
    [2]  /* soc            */ {0x6081, 0x00, 'B', 1.0},
    [3]  /* temperature    */ {0x2013, 0x01, 'h', 10.0},
    [4]  /* max_discharge  */ {0x5021, 0x01, 'i', 1000.0},
    [5]  /* max_charge     */ {0x5021, 0x02, 'i', 1000.0},
    [6]  /* max_chg_voltage*/ {0x2060, 0x00, 'I', 1024.0},
    [7]  /* cycles         */ {0x2014, 0x00, 'h', 1.0},
    [8]  /* capacity_ah    */ {0x2020, 0x00, 'H', 1.0},
    [9]  /* error_reg      */ {0x2004, 0x00, 'H', 1.0},
};
#define N_FAST_PARAMS    7   /* 0-6 */
#define N_SLOW_PARAMS    3   /* 7-9 */
#define N_SDO_PARAMS    10

/* ─── Global state ────────────────────────────────────────────────────── */

static int can_fd = -1;
static int dbus_fd = -1;
static int dbus_serial = 0;
static volatile sig_atomic_t running = 1;

/* Per-battery cached values */
typedef struct {
    int    node_id;
    double voltage, current, soc, temperature;
    double max_charge_a, max_discharge_a, max_charge_v;
    double capacity_ah;
    int    cycles;
    int    online;
} battery_state;

static battery_state batteries[3] = {
    {.node_id = 1, .online = 0},
    {.node_id = 2, .online = 0},
    {.node_id = 3, .online = 0},
};

/* ─── CAN helpers ──────────────────────────────────────────────────────── */

static int can_open(const char *ifname) {
    int fd = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if (fd < 0) {
        perror("socket(CAN)");
        return -1;
    }

    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);
    if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0) {
        perror("ioctl(SIOCGIFINDEX)");
        close(fd);
        return -1;
    }

    struct sockaddr_can {
        unsigned short family;
        unsigned short pad;
        int            ifindex;
    } addr = {.family = AF_CAN, .ifindex = ifr.ifr_ifindex};

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind(CAN)");
        close(fd);
        return -1;
    }
    return fd;
}

static int can_send(int fd, unsigned int id, const unsigned char *data, int len) {
    struct can_frame frame = {.can_id = id, .can_dlc = len};
    memcpy(frame.data, data, len);
    return write(fd, &frame, sizeof(frame));
}

static int can_recv(int fd, struct can_frame *frame, int timeout_ms) {
    struct pollfd pfd = {.fd = fd, .events = POLLIN};
    int ret = poll(&pfd, 1, timeout_ms);
    if (ret <= 0) return ret;
    return read(fd, frame, sizeof(*frame));
}

/* ─── SDO read ─────────────────────────────────────────────────────────── */

static int sdo_read(int fd, int node_id, unsigned short index,
                    unsigned char subindex, int *raw_out, int timeout_ms) {
    int sdo_tx = 0x600 + node_id;
    int sdo_rx = 0x580 + node_id;

    unsigned char req[8] = {
        0x40, index & 0xFF, (index >> 8) & 0xFF,
        subindex, 0, 0, 0, 0
    };
    if (can_send(fd, sdo_tx, req, 8) < 0) return -1;

    int deadline_ms = timeout_ms;
    struct timeval start, now;
    gettimeofday(&start, NULL);

    while (deadline_ms > 0) {
        struct can_frame resp;
        int ret = can_recv(fd, &resp, deadline_ms);
        if (ret <= 0) return -1;

        gettimeofday(&now, NULL);
        deadline_ms = timeout_ms - ((now.tv_sec - start.tv_sec) * 1000 +
                                     (now.tv_usec - start.tv_usec) / 1000);

        if (resp.can_id != (unsigned int)sdo_rx) continue;

        unsigned char cmd = resp.data[0];
        if (cmd == 0x80) return -2; /* abort */
        if (cmd == 0x43 || cmd == 0x47 || cmd == 0x4B || cmd == 0x4F || cmd == 0x41) {
            *raw_out = (int)(resp.data[4] | (resp.data[5] << 8) |
                            (resp.data[6] << 16) | (resp.data[7] << 24));
            return 0;
        }
    }
    return -1;
}

static double sdo_read_param(int fd, int node_id, const sdo_param *p,
                             int *aborted) {
    if (*aborted) return NAN;

    int raw;
    int ret = sdo_read(fd, node_id, p->index, p->subindex, &raw, 150);
    if (ret == -2) {
        *aborted = 1;
        return NAN;
    }
    if (ret < 0) return NAN;

    switch (p->dtype) {
    case 'i': return (double)(int)raw / p->divisor;
    case 'I': return (double)(unsigned int)raw / p->divisor;
    case 'h': return (double)(short)(raw & 0xFFFF) / p->divisor;
    case 'H': return (double)(unsigned short)(raw & 0xFFFF) / p->divisor;
    case 'B': return (double)(unsigned char)(raw & 0xFF) / p->divisor;
    }
    return NAN;
}

/* ─── D-Bus wire protocol (minimal, no libdbus) ───────────────────────── */

static int dbus_connect(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket(unix)"); return -1; }

    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    strcpy(addr.sun_path, DBUS_SYSTEM_BUS_PATH);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect(dbus)");
        close(fd);
        return -1;
    }

    /* Read server greeting: "OK <uuid>\r\n" (no null) */
    char buf[256];
    int n = read(fd, buf, sizeof(buf) - 1);
    if (n < 0) { close(fd); return -1; }
    buf[n] = '\0';
    if (strncmp(buf, "OK ", 3) != 0) {
        fprintf(stderr, "DBus greeting unexpected: %s\n", buf);
        close(fd); return -1;
    }

    /* Send AUTH: \0AUTH EXTERNAL <hex-uid>\r\n */
    char auth[128];
    int uid = getuid();
    auth[0] = '\0';
    int len = 1 + snprintf(auth + 1, sizeof(auth) - 1,
                           "AUTH EXTERNAL %x\r\n", uid);
    if (write(fd, auth, len) < 0) { close(fd); return -1; }

    /* Read AUTH response: "OK <guid>\r\n" */
    n = read(fd, buf, sizeof(buf) - 1);
    if (n < 0 || strncmp(buf, "OK ", 3) != 0) {
        buf[n < 0 ? 0 : n] = '\0';
        fprintf(stderr, "DBus auth failed: %s\n", buf);
        close(fd); return -1;
    }

    /* Send BEGIN */
    if (write(fd, "BEGIN\r\n", 7) < 0) { close(fd); return -1; }
    return fd;
}

/* Build D-Bus message header (little-endian).
 * Returns total message length; writes to buf. */
static int dbus_msg_header(unsigned char *buf, int serial,
                           const char *dest, const char *path,
                           const char *iface, const char *member) {
    memset(buf, 0, 256);
    /* Endianness flag: 'l' = little-endian */
    buf[0] = 'l';
    buf[1] = 0; /* message type 0 (will be set by caller) */
    buf[2] = 0; /* flags */
    buf[3] = 1; /* protocol version */

    /* Body length = 0 (no body, just header + padding) */
    /* We fill in the header fields after the fixed header */

    /* Skip 12 bytes of fixed header; we'll fill length later */
    int pos = 12; /* start of header fields */

    /* Header fields encoded as:
     *   struct { byte type; byte[1] sig; } header_field
     *   value (variant)
     *
     * Fields:
     *   PATH:    type=1, sig="o", value=path string
     *   DESTINATION: type=6, sig="s", value=dest string (only for method calls)
     *   INTERFACE: type=2, sig="s", value=iface string
     *   MEMBER:  type=3, sig="s", value=member string
     */

    /* PATH */
    buf[pos++] = 1; /* type PATH */
    buf[pos++] = 1; /* variant signature: 1 byte 'o' */
    buf[pos++] = 'o';
    buf[pos] = 0; /* padding */
    int path_len = strlen(path);
    buf[pos + 1] = path_len;
    memcpy(buf + pos + 5, path, path_len);
    pos += 5 + path_len;
    /* align to 8-byte boundary within header */
    while ((pos - 12) & 7) buf[pos++] = 0;

    /* DESTINATION (if present) */
    if (dest) {
        buf[pos++] = 6;
        buf[pos++] = 1;
        buf[pos++] = 's';
        buf[pos] = 0;
        int dlen = strlen(dest);
        buf[pos + 1] = dlen;
        memcpy(buf + pos + 5, dest, dlen);
        pos += 5 + dlen;
        while ((pos - 12) & 7) buf[pos++] = 0;
    }

    /* INTERFACE */
    buf[pos++] = 2;
    buf[pos++] = 1;
    buf[pos++] = 's';
    buf[pos] = 0;
    int ilen = strlen(iface);
    buf[pos + 1] = ilen;
    memcpy(buf + pos + 5, iface, ilen);
    pos += 5 + ilen;
    while ((pos - 12) & 7) buf[pos++] = 0;

    /* MEMBER */
    buf[pos++] = 3;
    buf[pos++] = 1;
    buf[pos++] = 's';
    buf[pos] = 0;
    int mlen = strlen(member);
    buf[pos + 1] = mlen;
    memcpy(buf + pos + 5, member, mlen);
    pos += 5 + mlen;
    while ((pos - 12) & 7) buf[pos++] = 0;

    /* Fill in header length (bytes 4-7) and body length (already 0) */
    int header_len = pos - 12;
    buf[4] = header_len & 0xFF;
    buf[5] = (header_len >> 8) & 0xFF;
    buf[6] = (header_len >> 16) & 0xFF;
    buf[7] = (header_len >> 24) & 0xFF;

    /* Serial number (bytes 8-11) */
    buf[8] = serial & 0xFF;
    buf[9] = (serial >> 8) & 0xFF;
    buf[10] = (serial >> 16) & 0xFF;
    buf[11] = (serial >> 24) & 0xFF;

    /* Marshal data area (empty for method call) — add 8-byte padding */
    int total = pos + 8;
    buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0;
    buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0;
    return total;
}

/* ─── D-Bus service registration ───────────────────────────────────────── */

/* Register a service name on the bus */
static int dbus_request_name(const char *name) {
    unsigned char buf[512];
    int serial = ++dbus_serial;
    int len = dbus_msg_header(buf, serial,
                              "org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "RequestName");
    buf[1] = DBUS_MESSAGE_TYPE_METHOD_CALL;

    /* Body: string name, uint32 flags */
    int pos = len - 8; /* data starts after header + padding */
    int nlen = strlen(name);
    buf[pos++] = nlen;
    memcpy(buf + pos, name, nlen);
    pos += nlen;
    buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0; /* flags=0 */
    buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0;

    /* fix body length */
    int body_len = pos - (len - 8);
    buf[4] = body_len & 0xFF;
    buf[5] = (body_len >> 8) & 0xFF;
    buf[6] = (body_len >> 16) & 0xFF;
    buf[7] = (body_len >> 24) & 0xFF;

    return write(dbus_fd, buf, pos);
}

/* Emit PropertiesChanged signal for a single property */
static int dbus_emit_property(const char *path, const char *iface,
                              const char *prop_name, int prop_type,
                              const void *value_ptr) {
    /*
     * Signal: org.freedesktop.DBus.Properties.PropertiesChanged
     * Body: STRING interface_name
     *       ARRAY of DICT_ENTRY(STRING, VARIANT) changed_properties
     *       ARRAY of STRING invalidated_properties
     *
     * For a single property:
     *   array length = 1
     *   dict entry: string key -> variant value
     */

    unsigned char buf[1024];
    int serial = ++dbus_serial;
    int header_pos = dbus_msg_header(buf, serial, NULL, path,
                                     "org.freedesktop.DBus.Properties",
                                     "PropertiesChanged");
    buf[1] = DBUS_MESSAGE_TYPE_SIGNAL;

    int pos = header_pos - 8; /* data starts here */

    /* ARG1: STRING interface_name */
    int ilen = strlen(iface);
    buf[pos++] = ilen;
    memcpy(buf + pos, iface, ilen);
    pos += ilen;
    buf[pos++] = 0; /* nul terminator */
    while (pos & 3) buf[pos++] = 0; /* align to 4 */

    /* ARG2: ARRAY of DICT_ENTRY(STRING, VARIANT) */
    /* array length = 12 (one dict entry: 4+4+4 alignment) */
    buf[pos++] = 12; buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0;
    /* aligned to 8-byte boundary? already 4-aligned, may need padding */
    while ((pos - (header_pos - 8)) & 7) buf[pos++] = 0;

    /* DICT_ENTRY: STRING key */
    int klen = strlen(prop_name);
    buf[pos++] = klen;
    memcpy(buf + pos, prop_name, klen);
    pos += klen;
    buf[pos++] = 0; /* nul */
    while (pos & 3) buf[pos++] = 0; /* align to 4 */

    /* VARIANT value: signature bytes + value */
    /* signature is 1 byte type + nul */
    char sig[2] = {prop_type, 0};
    buf[pos++] = 1; /* sig length = 1 */
    buf[pos++] = sig[0];
    buf[pos++] = 0; /* nul */
    buf[pos++] = 0; /* padding to 4-byte alignment */

    /* value — copy based on type */
    switch (prop_type) {
    case 'd': { /* double */
        double val = *(const double *)value_ptr;
        memcpy(buf + pos, &val, 8);
        pos += 8;
        break;
    }
    case 'i': { /* int32 */
        int val = *(const int *)value_ptr;
        buf[pos++] = val & 0xFF;
        buf[pos++] = (val >> 8) & 0xFF;
        buf[pos++] = (val >> 16) & 0xFF;
        buf[pos++] = (val >> 24) & 0xFF;
        break;
    }
    }

    /* ARG3: ARRAY of STRING (empty) */
    buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0; buf[pos++] = 0;

    /* Fix body length */
    int body_len = pos - (header_pos - 8);
    buf[4] = body_len & 0xFF;
    buf[5] = (body_len >> 8) & 0xFF;
    buf[6] = (body_len >> 16) & 0xFF;
    buf[7] = (body_len >> 24) & 0xFF;

    return write(dbus_fd, buf, pos);
}

/* ─── Signal handler ───────────────────────────────────────────────────── */

static void sig_handler(int sig) {
    (void)sig;
    running = 0;
}

/* ─── Main ─────────────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <can-interface>\n", argv[0]);
        return 1;
    }

    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);

    /* Connect CAN */
    can_fd = can_open(argv[1]);
    if (can_fd < 0) return 1;
    printf("victron-bms: connected to %s\n", argv[1]);

    /* Connect D-Bus */
    dbus_fd = dbus_connect();
    if (dbus_fd < 0) {
        fprintf(stderr, "D-Bus connect failed\n");
        return 1;
    }
    printf("victron-bms: connected to D-Bus\n");

    /* Register service names for each battery */
    for (int i = 0; i < 3; i++) {
        char name[128];
        snprintf(name, sizeof(name),
                 "com.victronenergy.battery.canopen_bms_node%d", i + 1);
        dbus_request_name(name);
        usleep(100000);
        printf("victron-bms: registered %s\n", name);
    }

    /* Abort tracking for SDO objects */
    int aborted[3][N_SDO_PARAMS];
    memset(aborted, 0, sizeof(aborted));

    int cycle = 0;
    struct timespec cycle_time;
    clock_gettime(CLOCK_MONOTONIC, &cycle_time);

    printf("victron-bms: running, 2000ms interval\n");

    while (running) {
        /* Read all fast params for all batteries */
        for (int b = 0; b < 3; b++) {
            double val;
            int nid = b + 1;

            for (int p = 0; p < N_FAST_PARAMS; p++) {
                val = sdo_read_param(can_fd, nid, &sdo_params[p], &aborted[b][p]);
                if (!isfinite(val)) continue;

                switch (p) {
                case 0: /* voltage */
                    batteries[b].voltage = val;
                    batteries[b].online = 1;
                    dbus_emit_property("/", "", "Connected", 'i', &(int){1});
                    dbus_emit_property("/", "Dc/0", "Voltage", 'd', &val);
                    break;
                case 1: /* current */
                    batteries[b].current = val;
                    dbus_emit_property("/", "Dc/0", "Current", 'd', &val);
                    { double power = batteries[b].voltage * val;
                      dbus_emit_property("/", "Dc/0", "Power", 'd', &power); }
                    break;
                case 2: /* soc */
                    batteries[b].soc = val;
                    dbus_emit_property("/", "", "Soc", 'd', &val);
                    { double consumed = 150.0 * (100.0 - val) / 100.0;
                      dbus_emit_property("/", "", "ConsumedAmphours", 'd', &consumed); }
                    break;
                case 4: /* max_discharge_a */
                    val = fabs(val);
                    dbus_emit_property("/", "Info", "MaxDischargeCurrent", 'd', &val);
                    break;
                case 5: /* max_charge_a */
                    val = fabs(val);
                    batteries[b].max_charge_a = val;
                    dbus_emit_property("/", "Info", "MaxChargeCurrent", 'd', &val);
                    break;
                case 6: /* max_charge_voltage */
                    batteries[b].max_charge_v = val;
                    dbus_emit_property("/", "Info", "MaxChargeVoltage", 'd', &val);
                    break;
                }
            }
        }

        /* Slow params every 10 cycles */
        if (cycle % 10 == 0) {
            for (int b = 0; b < 3; b++) {
                int nid = b + 1;
                for (int p = N_FAST_PARAMS; p < N_SDO_PARAMS; p++) {
                    double val = sdo_read_param(can_fd, nid, &sdo_params[p],
                                                &aborted[b][p]);
                    if (!isfinite(val)) continue;
                    switch (p) {
                    case 3: /* temperature */
                        dbus_emit_property("/", "Dc/0", "Temperature", 'd', &val);
                        break;
                    case 7: /* cycles */
                        { int c = (int)val;
                          dbus_emit_property("/", "History", "ChargeCycles", 'i', &c); }
                        break;
                    case 8: /* capacity_ah */
                        dbus_emit_property("/", "", "Capacity", 'd', &val);
                        break;
                    }
                }
            }
        }

        cycle++;

        /* Sleep until next 2-second boundary */
        cycle_time.tv_sec += 2;
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long sleep_ms = (cycle_time.tv_sec - now.tv_sec) * 1000 +
                        (cycle_time.tv_nsec - now.tv_nsec) / 1000000;
        if (sleep_ms > 0 && sleep_ms <= 2000) {
            usleep(sleep_ms * 1000);
        }
    }

    printf("victron-bms: shutting down\n");
    if (can_fd >= 0) close(can_fd);
    if (dbus_fd >= 0) close(dbus_fd);
    return 0;
}
