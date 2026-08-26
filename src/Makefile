CC ?= clang
CFLAGS ?= -O3 -Wall -Wextra

BIN_DIR = ../bin
TARGET = $(BIN_DIR)/mac-cpu-pct

all: $(TARGET)

$(TARGET): mac-cpu-pct.c
	@mkdir -p $(BIN_DIR)
	$(CC) $(CFLAGS) -o $(TARGET) mac-cpu-pct.c

clean:
	rm -f $(TARGET)

.PHONY: all clean
