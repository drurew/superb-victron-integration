/*
 * victron-bms.c - SuperB Epsilon V2 BMS Driver for Victron Venus OS
 *
 * Publishes battery data directly to D-Bus via libdbus-1, implementing
 * the com.victronenergy.BusItem interface.  Queries batteries via
 * CANopen/SDO at 250 kbps.  Registers up to 3 battery services.
 *
 * Build:  gcc -Os -std=c99 -Wall -Wextra -D_GNU_SOURCE \
 *             -I/usr/include/dbus-1.0 -I/usr/lib/dbus-1.0/include \
 *             -o victron-bms victron-bms.c -lm -ldbus-1
 * Run:    ./victron-bms vecan0
 *
 * License: MIT
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
#include <sys/ioctl.h>
#include <sys/time.h>
#include <net/if.h>
#include <poll.h>
#include <dbus/dbus.h>

#ifndef AF_CAN
#define AF_CAN 29
#endif
#ifndef PF_CAN
#define PF_CAN AF_CAN
#endif
#ifndef CAN_RAW
#define CAN_RAW 1
#endif
#ifndef SOCK_RAW
#define SOCK_RAW 3
#endif

typedef unsigned int canid_t;
struct can_frame { canid_t id; unsigned char dlc,__pad; unsigned short __res0; unsigned char data[8] __attribute__((aligned(8))); };

#define SERVICE "com.victronenergy.battery.superb_bms"
#define DEV_INST 280
#define VERSION "3.0.0"
#define CAPACITY 150.0
#define CELLS 16
#define TIMEOUT 5
#define MAX_NODES 3
#define FAST_MS 2000
#define SLOW_CYC 10

typedef struct { unsigned short idx,sub; char dt; double div; } sdo_t;
typedef struct { int node,online; double v,i,soc,temp,cvl,ccl,dcl,cap,cons; int cycles,err; double cell_v_min,cell_v_max,cell[CELLS]; int c_ok[CELLS]; time_t last; } bat_t;

/* SuperB Epsilon V2 CANopen SDO map */
static const sdo_t sf[] = {
    {0x6060,0,'i',1024.0},{0x2010,0,'i',1000.0},{0x6081,0,'B',1.0},
    {0x5021,1,'i',1000.0},{0x5021,2,'i',1000.0},{0x2060,0,'I',1024.0}};
static const sdo_t ss[] = {
    {0x2013,1,'h',10.0},{0x2014,0,'h',1.0},{0x2020,0,'H',1.0},{0x2004,0,'H',1.0}};
#define NF (sizeof(sf)/sizeof(sf[0]))
#define NS (sizeof(ss)/sizeof(ss[0]))

static bat_t B[MAX_NODES];
static volatile int run=1;
static DBusConnection *conn;

static int can_open(const char *n){
    int fd=socket(PF_CAN,SOCK_RAW,CAN_RAW); if(fd<0){perror("socket");return -1;}
    struct ifreq r; memset(&r,0,sizeof(r)); strncpy(r.ifr_name,n,IFNAMSIZ-1);
    if(ioctl(fd,SIOCGIFINDEX,&r)<0){perror("ioctl");close(fd);return -1;}
    struct{unsigned short f,p;int i;}a={AF_CAN,0,r.ifr_ifindex};
    if(bind(fd,(struct sockaddr*)&a,sizeof(a))<0){perror("bind");close(fd);return -1;}
    return fd;
}
static int can_send(int fd,unsigned id,const unsigned char*d,int l){
    struct can_frame f={.id=id,.dlc=l}; memcpy(f.data,d,l);
    return write(fd,&f,sizeof(f));
}
static int can_recv(int fd,struct can_frame*f,int ms){
    struct pollfd p={.fd=fd,.events=POLLIN};
    if(poll(&p,1,ms)<=0){dbus_connection_read_write_dispatch(conn,0);return 0;}
    return read(fd,f,sizeof(*f))==sizeof(*f)?1:0;
}
static int sdo_read(int fd,int node,unsigned short idx,unsigned char sub,int*o,int ms){
    int tx=0x600+node,rx=0x580+node;
    unsigned char req[8]={0x40,idx&0xFF,idx>>8,sub,0,0,0,0};
    if(can_send(fd,tx,req,8)<0)return -1;
    struct timeval s; gettimeofday(&s,NULL);
    int d=ms;
    while(d>0){struct can_frame r;int ret=can_recv(fd,&r,d);
        if(ret<=0)return -1;
        struct timeval n; gettimeofday(&n,NULL);
        d=ms-((n.tv_sec-s.tv_sec)*1000+(n.tv_usec-s.tv_usec)/1000);
        if(r.id!=(unsigned)rx)continue;
        if(r.data[0]==0x80)return -2;
        if(r.data[0]==0x43||r.data[0]==0x47||r.data[0]==0x4B||r.data[0]==0x4F||r.data[0]==0x41)
        {*o=(int)(r.data[4]|(r.data[5]<<8)|(r.data[6]<<16)|(r.data[7]<<24));return 0;}
    }return -1;
}
static double sdo_val(int fd,int node,const sdo_t*p,int*ab){
    if(*ab)return NAN;
    int raw,r=sdo_read(fd,node,p->idx,p->sub,&raw,150);
    if(r==-2){*ab=1;return NAN;}if(r<0)return NAN;
    switch(p->dt){
    case'i':return(int)raw/p->div;case'I':return(unsigned)raw/p->div;
    case'h':return(short)(raw&0xFFFF)/p->div;case'H':return(unsigned short)(raw&0xFFFF)/p->div;
    case'B':return(unsigned char)(raw&0xFF)/p->div;}return NAN;
}

/* D-Bus: append {path: {Value:variant, Text:string}} dict entry */
static void ae(DBusMessageIter*A,const char*p,int vt,const void*v,const char*t){
    DBusMessageIter e,d,de,sv;
    dbus_message_iter_open_container(A,DBUS_TYPE_DICT_ENTRY,NULL,&e);
    dbus_message_iter_append_basic(&e,DBUS_TYPE_STRING,&p);
    dbus_message_iter_open_container(&e,DBUS_TYPE_ARRAY,"{sv}",&d);
    dbus_message_iter_open_container(&d,DBUS_TYPE_DICT_ENTRY,NULL,&de);
    const char*k="Value";dbus_message_iter_append_basic(&de,DBUS_TYPE_STRING,&k);
    const char*sig=vt==DBUS_TYPE_DOUBLE?"d":vt==DBUS_TYPE_INT32?"i":"s";
    dbus_message_iter_open_container(&de,DBUS_TYPE_VARIANT,sig,&sv);
    if(vt==DBUS_TYPE_STRING){const char*sp=(const char*)v;dbus_message_iter_append_basic(&sv,vt,&sp);}
    else dbus_message_iter_append_basic(&sv,vt,v);
    dbus_message_iter_close_container(&de,&sv);dbus_message_iter_close_container(&d,&de);
    dbus_message_iter_open_container(&d,DBUS_TYPE_DICT_ENTRY,NULL,&de);
    const char*n="Text";dbus_message_iter_append_basic(&de,DBUS_TYPE_STRING,&n);
    dbus_message_iter_open_container(&de,DBUS_TYPE_VARIANT,"s",&sv);
    dbus_message_iter_append_basic(&sv,DBUS_TYPE_STRING,&t);
    dbus_message_iter_close_container(&de,&sv);dbus_message_iter_close_container(&d,&de);
    dbus_message_iter_close_container(&e,&d);dbus_message_iter_close_container(A,&e);
}

static void a_all(DBusMessageIter*A,bat_t*b){
    char buf[64]; double d; int iv; const char*s;
    #define E(p,t,vv,tt) ae(A,p,t,vv,tt)
    /* Measurements */
    d=b->soc; snprintf(buf,sizeof(buf),"%.0f%%",d); E("/Soc",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->v; snprintf(buf,sizeof(buf),"%.2fV",d); E("/Dc/0/Voltage",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->i; snprintf(buf,sizeof(buf),"%.1fA",d); E("/Dc/0/Current",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->v*b->i; snprintf(buf,sizeof(buf),"%.0fW",d); E("/Dc/0/Power",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->temp; snprintf(buf,sizeof(buf),"%.1f°C",d); E("/Dc/0/Temperature",DBUS_TYPE_DOUBLE,&d,buf);
    /* DVCC limits */
    d=b->ccl; snprintf(buf,sizeof(buf),"%.1fA",d); E("/Info/MaxChargeCurrent",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->dcl; snprintf(buf,sizeof(buf),"%.1fA",d); E("/Info/MaxDischargeCurrent",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->cvl; snprintf(buf,sizeof(buf),"%.2fV",d); E("/Info/MaxChargeVoltage",DBUS_TYPE_DOUBLE,&d,buf);
    /* Cell stats */
    d=b->cell_v_min; snprintf(buf,sizeof(buf),"%.3fV",d); E("/System/MinCellVoltage",DBUS_TYPE_DOUBLE,&d,buf);
    d=b->cell_v_max; snprintf(buf,sizeof(buf),"%.3fV",d); E("/System/MaxCellVoltage",DBUS_TYPE_DOUBLE,&d,buf);
    /* System */
    iv=CELLS; snprintf(buf,sizeof(buf),"%d",iv); E("/System/NrOfCellsPerBattery",DBUS_TYPE_INT32,&iv,buf);
    iv=b->online?1:0; snprintf(buf,sizeof(buf),"%d",iv); E("/System/NrOfModulesOnline",DBUS_TYPE_INT32,&iv,buf);
    iv=b->online?0:1; snprintf(buf,sizeof(buf),"%d",iv); E("/System/NrOfModulesOffline",DBUS_TYPE_INT32,&iv,buf);
    iv=0; snprintf(buf,sizeof(buf),"%d",iv); E("/System/NrOfModulesBlockingCharge",DBUS_TYPE_INT32,&iv,buf);
    iv=0; snprintf(buf,sizeof(buf),"%d",iv); E("/System/NrOfModulesBlockingDischarge",DBUS_TYPE_INT32,&iv,buf);
    /* Connection */
    iv=b->online; snprintf(buf,sizeof(buf),"%d",iv); E("/Connected",DBUS_TYPE_INT32,&iv,buf);
    iv=b->online; snprintf(buf,sizeof(buf),"%d",iv); E("/Io/AllowToCharge",DBUS_TYPE_INT32,&iv,buf);
    iv=b->online; snprintf(buf,sizeof(buf),"%d",iv); E("/Io/AllowToDischarge",DBUS_TYPE_INT32,&iv,buf);
    iv=b->online; snprintf(buf,sizeof(buf),"%d",iv); E("/Capabilities/ChargeVoltageControl",DBUS_TYPE_INT32,&iv,buf);
    /* History */
    d=b->cons; snprintf(buf,sizeof(buf),"%.1f",d); E("/ConsumedAmphours",DBUS_TYPE_DOUBLE,&d,buf);
    iv=DEV_INST; snprintf(buf,sizeof(buf),"%d",iv); E("/DeviceInstance",DBUS_TYPE_INT32,&iv,buf);
    d=b->cap; snprintf(buf,sizeof(buf),"%.0f",d); E("/InstalledCapacity",DBUS_TYPE_DOUBLE,&d,buf);
    iv=b->cycles; snprintf(buf,sizeof(buf),"%d",iv); E("/History/ChargeCycles",DBUS_TYPE_INT32,&iv,buf);
    /* Metadata */
    E("/ProductName",DBUS_TYPE_STRING,"SuperB Epsilon V2","SuperB Epsilon V2");
    E("/HardwareVersion",DBUS_TYPE_STRING,"Epsilon V2","Epsilon V2");
    E("/FirmwareVersion",DBUS_TYPE_STRING,VERSION,VERSION);
    E("/Mgmt/ProcessName",DBUS_TYPE_STRING,"victron-bms","victron-bms");
    E("/Mgmt/ProcessVersion",DBUS_TYPE_STRING,VERSION,VERSION);
    E("/Mgmt/Connection",DBUS_TYPE_STRING,"CANopen SDO","CANopen SDO");
    /* Alarms - SuperB error register bit mapping */
    int e=b->err;
    iv=(e&0x0001)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/LowVoltage",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0002)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/HighVoltage",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0008)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/LowTemperature",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0004)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/HighTemperature",DBUS_TYPE_INT32,&iv,buf);
    iv=b->online&&b->soc<10.0?1:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/LowSoc",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0020)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/HighChargeCurrent",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0010)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/HighDischargeCurrent",DBUS_TYPE_INT32,&iv,buf);
    iv=0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/CellImbalance",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0040)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/InternalFailure",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0004)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/HighChargeTemperature",DBUS_TYPE_INT32,&iv,buf);
    iv=(e&0x0008)?2:0; snprintf(buf,sizeof(buf),"%d",iv); E("/Alarms/LowChargeTemperature",DBUS_TYPE_INT32,&iv,buf);
    /* Per-cell voltages */
    for(int n=0;n<CELLS;n++)if(b->c_ok[n]){
        char cp[32],ct[16]; snprintf(cp,sizeof(cp),"/Voltages/Cell%d",n+1);
        d=b->cell[n]; snprintf(ct,sizeof(ct),"%.3fV",d);
        ae(A,cp,DBUS_TYPE_DOUBLE,&d,ct);
    }
    #undef E
}

static void emit(bat_t*b){
    DBusMessage*s=dbus_message_new_signal("/","com.victronenergy.BusItem","ItemsChanged");
    if(!s)return;
    DBusMessageIter it,arr;
    dbus_message_iter_init_append(s,&it);
    dbus_message_iter_open_container(&it,DBUS_TYPE_ARRAY,"{sa{sv}}",&arr);
    a_all(&arr,b);
    dbus_message_iter_close_container(&it,&arr);
    dbus_connection_send(conn,s,NULL);dbus_connection_flush(conn);
    dbus_message_unref(s);
}

static DBusHandlerResult on_msg(DBusConnection*c,DBusMessage*m,void*u){
    fprintf(stderr,"on_msg called\n"); fflush(stderr);
    const char*dest=dbus_message_get_destination(m);
    bat_t*b=&B[0];
    if(dest){for(int i=0;i<MAX_NODES;i++){char svc[128];snprintf(svc,sizeof(svc),"%s_node%d",SERVICE,i+1);if(!strcmp(dest,svc)){b=&B[i];break;}}}
    if(dbus_message_is_method_call(m,"org.freedesktop.DBus.Introspectable","Introspect")){
        const char*xml="<!DOCTYPE node PUBLIC \"-//freedesktop//DTD D-BUS Object Introspection 1.0//EN\"\n\"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd\">\n<node><interface name=\"com.victronenergy.BusItem\"><method name=\"GetValue\"><arg direction=\"out\" type=\"v\"/></method><method name=\"GetText\"><arg direction=\"out\" type=\"s\"/></method><method name=\"GetItems\"><arg direction=\"out\" type=\"a{sa{sv}}\"/></method><signal name=\"ItemsChanged\"><arg type=\"a{sa{sv}}\" name=\"changes\"/></signal></interface></node>\n";
        DBusMessage*r=dbus_message_new_method_return(m);
        dbus_message_append_args(r,DBUS_TYPE_STRING,&xml,DBUS_TYPE_INVALID);
        dbus_connection_send(c,r,NULL);dbus_message_unref(r);
        return DBUS_HANDLER_RESULT_HANDLED;
    }
    const char*path=dbus_message_get_path(m),*method=dbus_message_get_member(m);
    if(!method||strcmp(dbus_message_get_interface(m),"com.victronenergy.BusItem"))
        return DBUS_HANDLER_RESULT_NOT_YET_HANDLED;

    if(!strcmp(method,"GetItems")){
        DBusMessage*r=dbus_message_new_method_return(m);
        DBusMessageIter it,arr;
        dbus_message_iter_init_append(r,&it);
        dbus_message_iter_open_container(&it,DBUS_TYPE_ARRAY,"{sa{sv}}",&arr);
        a_all(&arr,b);
        dbus_message_iter_close_container(&it,&arr);
        dbus_connection_send(c,r,NULL);dbus_message_unref(r);
        return DBUS_HANDLER_RESULT_HANDLED;
    }
    /* GetValue/GetText: rebuild the list inline.  For a small fixed set this is fine. */
    char buf[64]; double d; int iv; const char*s;
    #define TRY(p,vt,vv,tt) if(!strcmp(path,p)){if(!strcmp(method,"GetValue")){DBusMessage*r=dbus_message_new_method_return(m);DBusMessageIter it;dbus_message_iter_init_append(r,&it);DBusMessageIter sv;const char*sig=vt==DBUS_TYPE_DOUBLE?"d":vt==DBUS_TYPE_INT32?"i":"s";dbus_message_iter_open_container(&it,DBUS_TYPE_VARIANT,sig,&sv);dbus_message_iter_append_basic(&sv,vt,vv);dbus_message_iter_close_container(&it,&sv);dbus_connection_send(c,r,NULL);dbus_message_unref(r);}else{DBusMessage*r=dbus_message_new_method_return(m);dbus_message_append_args(r,DBUS_TYPE_STRING,&tt,DBUS_TYPE_INVALID);dbus_connection_send(c,r,NULL);dbus_message_unref(r);}return DBUS_HANDLER_RESULT_HANDLED;}
    #define TD(p,expr,fmt) d=expr;snprintf(buf,sizeof(buf),fmt,d);TRY(p,DBUS_TYPE_DOUBLE,&d,buf)
    #define TI(p,expr) iv=expr;snprintf(buf,sizeof(buf),"%d",iv);TRY(p,DBUS_TYPE_INT32,&iv,buf)
    #define TS(p,str) s=str;TRY(p,DBUS_TYPE_STRING,s,s)
    TD("/Soc",b->soc,"%.0f%%");TD("/Dc/0/Voltage",b->v,"%.2fV");TD("/Dc/0/Current",b->i,"%.1fA");
    TD("/Dc/0/Power",b->v*b->i,"%.0fW");TD("/Dc/0/Temperature",b->temp,"%.1f°C");
    TD("/Info/MaxChargeCurrent",b->ccl,"%.1fA");TD("/Info/MaxDischargeCurrent",b->dcl,"%.1fA");
    TD("/Info/MaxChargeVoltage",b->cvl,"%.2fV");
    TD("/System/MinCellVoltage",b->cell_v_min,"%.3fV");TD("/System/MaxCellVoltage",b->cell_v_max,"%.3fV");
    TI("/System/NrOfCellsPerBattery",CELLS);TI("/System/NrOfModulesOnline",b->online?1:0);
    TI("/System/NrOfModulesOffline",b->online?0:1);
    TI("/System/NrOfModulesBlockingCharge",0);TI("/System/NrOfModulesBlockingDischarge",0);
    TI("/Connected",b->online);TI("/Io/AllowToCharge",b->online);TI("/Io/AllowToDischarge",b->online);
    TI("/Capabilities/ChargeVoltageControl",b->online);
    TD("/ConsumedAmphours",b->cons,"%.1f");TI("/DeviceInstance",DEV_INST);
    TD("/InstalledCapacity",b->cap,"%.0f");TI("/History/ChargeCycles",b->cycles);
    TS("/ProductName","SuperB Epsilon V2");TS("/HardwareVersion","Epsilon V2");
    TS("/FirmwareVersion",VERSION);TS("/Mgmt/ProcessName","victron-bms");
    TS("/Mgmt/ProcessVersion",VERSION);TS("/Mgmt/Connection","CANopen SDO");
    int e=b->err;
    TI("/Alarms/LowVoltage",(e&0x0001)?2:0);TI("/Alarms/HighVoltage",(e&0x0002)?2:0);
    TI("/Alarms/LowTemperature",(e&0x0008)?2:0);TI("/Alarms/HighTemperature",(e&0x0004)?2:0);
    TI("/Alarms/LowSoc",b->online&&b->soc<10.0?1:0);
    TI("/Alarms/HighChargeCurrent",(e&0x0020)?2:0);TI("/Alarms/HighDischargeCurrent",(e&0x0010)?2:0);
    TI("/Alarms/CellImbalance",0);TI("/Alarms/InternalFailure",(e&0x0040)?2:0);
    TI("/Alarms/HighChargeTemperature",(e&0x0004)?2:0);TI("/Alarms/LowChargeTemperature",(e&0x0008)?2:0);
    /* Per-cell voltages */
    if(strncmp(path,"/Voltages/Cell",14)==0){int n=atoi(path+14);
        if(n>=1&&n<=CELLS&&b->c_ok[n-1]){d=b->cell[n-1];snprintf(buf,sizeof(buf),"%.3fV",d);TRY(path,DBUS_TYPE_DOUBLE,&d,buf);}}
    #undef TRY
    #undef TD
    #undef TI
    #undef TS
    DBusMessage*err=dbus_message_new_error(m,"com.victronenergy.BusItem.Error","Path not found");
    dbus_connection_send(c,err,NULL);dbus_message_unref(err);
    return DBUS_HANDLER_RESULT_HANDLED;
}

static int dbus_reg(int node,bat_t*b){
    char svc[128]; snprintf(svc,sizeof(svc),"%s_node%d",SERVICE,node);
    DBusError e; dbus_error_init(&e);
    int r=dbus_bus_request_name(conn,svc,DBUS_NAME_FLAG_DO_NOT_QUEUE,&e);
    if(r!=DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER){fprintf(stderr,"dbus: %s taken\n",svc);return -1;}
    printf("victron-bms: registered %s\n",svc);
    return 0;
}

static void on_signal(int sig){(void)sig;run=0;}

int main(int argc,char**argv){
    const char*ifname=NULL;
    for(int i=1;i<argc;i++)if(argv[i][0]!='-')ifname=argv[i];
    if(!ifname){fprintf(stderr,"Usage: %s <can-interface>\n",argv[0]);return 1;}
    signal(SIGINT,on_signal);signal(SIGTERM,on_signal);signal(SIGPIPE,SIG_IGN);
    setbuf(stdout,NULL);

    int cfd=can_open(ifname);if(cfd<0)return 1;
    printf("victron-bms: CAN on %s\n",ifname);

    DBusError de; dbus_error_init(&de);
    conn=dbus_bus_get(DBUS_BUS_SYSTEM,&de);
    if(dbus_error_is_set(&de)){fprintf(stderr,"dbus: %s\n",de.message);return 1;}

    /* Match rules and filter are per-connection, added once */
    dbus_bus_add_match(conn,"type='method_call',interface='com.victronenergy.BusItem'",&de);
    dbus_bus_add_match(conn,"type='method_call',interface='org.freedesktop.DBus.Introspectable'",&de);
    dbus_connection_add_filter(conn,on_msg,NULL,NULL);

    for(int i=0;i<MAX_NODES;i++){B[i].node=i+1;B[i].online=0;B[i].cap=CAPACITY;}
    for(int i=0;i<MAX_NODES;i++){if(dbus_reg(i+1,&B[i])<0){close(cfd);return 1;}emit(&B[i]);}

    printf("victron-bms: running, %dms interval\n",FAST_MS);

    int aborted[MAX_NODES]; memset(aborted,0,sizeof(aborted));
    int cycle=0; struct timespec next; clock_gettime(CLOCK_MONOTONIC,&next);

    while(run){
        dbus_connection_read_write_dispatch(conn,0);
        time_t now=time(NULL);
        for(int n=0;n<MAX_NODES;n++){double v;
            for(int p=0;p<(int)NF;p++){v=sdo_val(cfd,n+1,&sf[p],&aborted[n]);if(!isfinite(v))continue;
                switch(p){
                case 0:B[n].v=v;B[n].online=1;B[n].last=now;break;
                case 1:B[n].i=v;B[n].cons=B[n].cap*(100.0-B[n].soc)/100.0;break;
                case 2:B[n].soc=v;break;
                case 3:B[n].dcl=fabs(v);break;
                case 4:B[n].ccl=fabs(v);break;
                case 5:B[n].cvl=v;break;
                }}}
        if(cycle%SLOW_CYC==0)for(int n=0;n<MAX_NODES;n++){double v;
            for(int p=0;p<(int)NS;p++){v=sdo_val(cfd,n+1,&ss[p],&aborted[n]);if(!isfinite(v))continue;
                switch(p){
                case 0:B[n].temp=v;break;
                case 1:B[n].cycles=(int)v;break;
                case 2:B[n].cap=v;break;
                case 3:B[n].err=(int)v;break;
                }}}
        for(int n=0;n<MAX_NODES;n++)if(B[n].online&&now-B[n].last>TIMEOUT){B[n].online=0;printf("victron-bms: node %d timeout\n",n+1);}
        for(int n=0;n<MAX_NODES;n++)emit(&B[n]);
        cycle++;
        next.tv_sec+=FAST_MS/1000; struct timespec cur; clock_gettime(CLOCK_MONOTONIC,&cur);
        long ms=(next.tv_sec-cur.tv_sec)*1000+(next.tv_nsec-cur.tv_nsec)/1000000;
        if(ms>0&&ms<=FAST_MS)usleep(ms*1000);
        dbus_connection_read_write_dispatch(conn,50);
    }
    printf("victron-bms: shutting down\n");
    for(int n=0;n<MAX_NODES;n++){char svc[128];snprintf(svc,sizeof(svc),"%s_node%d",SERVICE,n+1);dbus_bus_release_name(conn,svc,NULL);}
    dbus_connection_unref(conn);close(cfd);return 0;
}
