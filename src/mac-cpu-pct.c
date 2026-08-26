/*
 * mac-cpu-pct.c - High-accuracy, low-overhead CPU usage monitor for macOS.
 *
 * Uses Mach host statistics (host_statistics64 with HOST_CPU_LOAD_INFO) to calculate
 * exact CPU tick deltas between calls, writing state to /tmp/.tmux_cpu_ticks_<user>.
 *
 * Compilation:
 *   clang -O3 -Wall -Wextra -o mac-cpu-pct mac-cpu-pct.c
 */

#include <mach/mach.h>
#include <mach/mach_host.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    natural_t count = HOST_CPU_LOAD_INFO_COUNT;
    host_cpu_load_info_data_t r_load;
    mach_port_t host = mach_host_self();

    if (host_statistics64(host, HOST_CPU_LOAD_INFO, (host_info64_t)&r_load, &count) != KERN_SUCCESS) {
        return 1;
    }

    const char *user = getenv("USER");
    if (!user || user[0] == '\0') {
        user = "default";
    }

    char path[512];
    snprintf(path, sizeof(path), "/tmp/.tmux_cpu_ticks_%s", user);

    unsigned long long prev_u = 0, prev_s = 0, prev_i = 0, prev_n = 0;
    FILE *f = fopen(path, "r");
    if (f) {
        if (fscanf(f, "%llu %llu %llu %llu", &prev_u, &prev_s, &prev_i, &prev_n) != 4) {
            prev_u = prev_s = prev_i = prev_n = 0;
        }
        fclose(f);
    }

    /* Save current ticks */
    f = fopen(path, "w");
    if (f) {
        fprintf(f, "%llu %llu %llu %llu\n",
            (unsigned long long)r_load.cpu_ticks[CPU_STATE_USER],
            (unsigned long long)r_load.cpu_ticks[CPU_STATE_SYSTEM],
            (unsigned long long)r_load.cpu_ticks[CPU_STATE_IDLE],
            (unsigned long long)r_load.cpu_ticks[CPU_STATE_NICE]);
        fclose(f);
    }

    /* Calculate delta usage */
    unsigned long long cur_u = (unsigned long long)r_load.cpu_ticks[CPU_STATE_USER];
    unsigned long long cur_s = (unsigned long long)r_load.cpu_ticks[CPU_STATE_SYSTEM];
    unsigned long long cur_i = (unsigned long long)r_load.cpu_ticks[CPU_STATE_IDLE];
    unsigned long long cur_n = (unsigned long long)r_load.cpu_ticks[CPU_STATE_NICE];

    if (prev_u == 0 && prev_s == 0 && prev_i == 0 && prev_n == 0) {
        /* First run or missing state - print nothing so fallback can take over */
        return 0;
    }

    unsigned long long du = (cur_u >= prev_u) ? (cur_u - prev_u) : 0;
    unsigned long long ds = (cur_s >= prev_s) ? (cur_s - prev_s) : 0;
    unsigned long long di = (cur_i >= prev_i) ? (cur_i - prev_i) : 0;
    unsigned long long dn = (cur_n >= prev_n) ? (cur_n - prev_n) : 0;
    unsigned long long total = du + ds + di + dn;

    if (total > 0) {
        unsigned long long used = du + ds + dn;
        int pct = (int)((used * 100 + total / 2) / total);
        if (pct < 0) pct = 0;
        if (pct > 100) pct = 100;
        printf("%d\n", pct);
    }

    return 0;
}
