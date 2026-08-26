CC ?= clang
CFLAGS ?= -O3 -Wall -Wextra
PREFIX ?= $(HOME)/.local

all: bin/mac-cpu-pct

bin/mac-cpu-pct: src/mac-cpu-pct.c
	@mkdir -p bin
	$(CC) $(CFLAGS) -o bin/mac-cpu-pct src/mac-cpu-pct.c

install-bin: all
	@mkdir -p $(PREFIX)/bin
	cp bin/mac-cpu-pct $(PREFIX)/bin/
	cp bin/sentinel $(PREFIX)/bin/

install: install-bin
	@python3 bin/sentinel install

test: all
	@python3 bin/sentinel preview
	@scripts/status-right.sh

clean:
	rm -f bin/mac-cpu-pct

.PHONY: all install install-bin test clean
