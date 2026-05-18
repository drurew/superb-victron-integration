# SuperB Epsilon V2 — Victron Cerbo GX Integration
# Build the C driver (native compilation on Cerbo GX)

.PHONY: all clean install

CC      := gcc
CFLAGS  := -Os -s -Wall -Wextra -std=c99
LDLIBS  := -lm
TARGET  := victron-bms
SRCDIR  := src

all: $(TARGET)

$(TARGET): $(SRCDIR)/victron-bms.c
	$(CC) $(CFLAGS) $(LDLIBS) -o $@ $<

clean:
	rm -f $(TARGET)

install: $(TARGET)
	install -d /data/bms
	install -m 755 $(TARGET) /data/bms/
	@echo "Installed $(TARGET) to /data/bms/"
