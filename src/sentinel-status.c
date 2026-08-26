#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#ifdef __APPLE__
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/ps/IOPowerSources.h>
#include <IOKit/ps/IOPSKeys.h>
#include <IOKit/pwr_mgt/IOPMLib.h>
#include <libkern/OSThermalNotification.h>
#include <mach/mach.h>
#include <mach/mach_host.h>
#include <notify.h>
#include <sys/mount.h>
#include <sys/sysctl.h>
extern CFDictionaryRef IOPMCopyPMPreferences(void);
extern CFDictionaryRef IOPMCopySystemPowerSettings(void);
#elif defined(__linux__)
#include <sys/statvfs.h>
#else
#error "sentinel-status supports only Darwin and Linux"
#endif

#define VERSION "0.2.0"
#define ARRAY_LEN(a) (sizeof(a) / sizeof((a)[0]))
#define CPU_STATE_MAGIC UINT32_C(0x534e5432)
#define CPU_STATE_VERSION 1U
#define NS_PER_SEC UINT64_C(1000000000)
#define CPU_CACHE_NS (UINT64_C(500) * UINT64_C(1000000))
#define CPU_STALE_NS (UINT64_C(30) * NS_PER_SEC)
#define CPU_SAMPLE_NS (UINT64_C(80) * UINT64_C(1000000))

struct config {
    int alerts_only;
    char clock_format[129];
    char color_fg[32], color_dim[32], color_val[32], color_sep[32];
    char color_alert[32], color_warn[32], color_peach[32], color_info[32];
    int seg_thermal, seg_sleep_risk, seg_disk, seg_battery;
    int seg_cpu, seg_memory, seg_multi_client, seg_clock;
    char glyph_sep[129], glyph_thermal[33], glyph_sleep[33], glyph_disk[33];
    char glyph_battery_full[33], glyph_battery_mid[33], glyph_battery_low[33];
    char glyph_cpu[33], glyph_memory[33], glyph_clients[33];
    int disk_warn_gb, disk_crit_gb, cpu_warn_pct, cpu_crit_pct;
    int battery_warn_pct, battery_crit_pct;
};

struct metrics {
    bool thermal_ok, thermal_alert;
    int thermal_value;
    char thermal_word[16];
    bool sleep_ok, sleep_risk;
    int sleep_minutes;
    bool disk_ok;
    int disk_free_gb;
    bool battery_ok, battery_present, battery_discharging;
    int battery_pct;
    bool cpu_ok;
    int cpu_pct;
    bool memory_ok;
    double swap_gb;
    int memory_pressure;
    bool clock_ok;
    char clock[65];
    int clients;
};

struct cpu_sample {
    uint64_t user, system, idle, nice;
};

struct cpu_record {
    uint32_t magic, version;
    uint64_t timestamp_ns;
    struct cpu_sample sample;
    int32_t percentage;
    uint32_t reserved;
};

struct outbuf {
    char data[8192];
    size_t len;
    bool truncated;
};

static void copy_string(char *dst, size_t size, const char *src)
{
    if (size == 0) return;
    (void)snprintf(dst, size, "%s", src);
}

static void config_defaults(struct config *c)
{
    memset(c, 0, sizeof(*c));
    c->alerts_only = 1;
    copy_string(c->clock_format, sizeof(c->clock_format), "%H:%M");
    copy_string(c->color_fg, sizeof(c->color_fg), "#cdd6f4");
    copy_string(c->color_dim, sizeof(c->color_dim), "#6c7086");
    copy_string(c->color_val, sizeof(c->color_val), "#a6adc8");
    copy_string(c->color_sep, sizeof(c->color_sep), "#45475a");
    copy_string(c->color_alert, sizeof(c->color_alert), "#f38ba8");
    copy_string(c->color_warn, sizeof(c->color_warn), "#f9e2af");
    copy_string(c->color_peach, sizeof(c->color_peach), "#fab387");
    copy_string(c->color_info, sizeof(c->color_info), "#94e2d5");
    c->seg_thermal = c->seg_sleep_risk = c->seg_disk = c->seg_battery = 1;
    c->seg_cpu = c->seg_memory = c->seg_multi_client = c->seg_clock = 1;
    copy_string(c->glyph_sep, sizeof(c->glyph_sep), " · ");
    copy_string(c->glyph_thermal, sizeof(c->glyph_thermal), "");
    copy_string(c->glyph_sleep, sizeof(c->glyph_sleep), "");
    copy_string(c->glyph_disk, sizeof(c->glyph_disk), "");
    copy_string(c->glyph_battery_full, sizeof(c->glyph_battery_full), "");
    copy_string(c->glyph_battery_mid, sizeof(c->glyph_battery_mid), "");
    copy_string(c->glyph_battery_low, sizeof(c->glyph_battery_low), "");
    copy_string(c->glyph_cpu, sizeof(c->glyph_cpu), "");
    copy_string(c->glyph_memory, sizeof(c->glyph_memory), "󰍛");
    copy_string(c->glyph_clients, sizeof(c->glyph_clients), "");
    c->disk_warn_gb = 25;
    c->disk_crit_gb = 15;
    c->cpu_warn_pct = 70;
    c->cpu_crit_pct = 90;
    c->battery_warn_pct = 50;
    c->battery_crit_pct = 20;
}

static bool parse_integer(const char *s, int min, int max, int *value)
{
    char *end = NULL;
    long parsed;
    if (*s == '\0' || isspace((unsigned char)*s)) return false;
    errno = 0;
    parsed = strtol(s, &end, 10);
    if (errno != 0 || *end != '\0' || parsed < min || parsed > max) return false;
    *value = (int)parsed;
    return true;
}

static bool parse_clamped_integer(const char *s, int min, int max, int *value)
{
    char *end = NULL;
    long parsed;
    if (*s == '\0' || isspace((unsigned char)*s)) return false;
    errno = 0;
    parsed = strtol(s, &end, 10);
    if (*end != '\0') return false;
    if (errno == ERANGE) parsed = *s == '-' ? LONG_MIN : LONG_MAX;
    else if (errno != 0) return false;
    if (parsed < min) parsed = min;
    if (parsed > max) parsed = max;
    *value = (int)parsed;
    return true;
}

static void set_bool(const char *value, int *target)
{
    int parsed;
    if (parse_integer(value, 0, 1, &parsed)) *target = parsed;
}

static void parse_config_value(struct config *c, const char *key, const char *value)
{
#define STRING_KEY(name, field) if (strcmp(key, name) == 0) { copy_string(c->field, sizeof(c->field), value); return; }
#define BOOL_KEY(name, field) if (strcmp(key, name) == 0) { set_bool(value, &c->field); return; }
#define INT_KEY(name, field, lo, hi) if (strcmp(key, name) == 0) { int v; if (parse_clamped_integer(value, lo, hi, &v)) c->field = v; return; }
    if (strcmp(key, "version") == 0) return;
    BOOL_KEY("alerts_only", alerts_only)
    STRING_KEY("clock_format", clock_format)
    STRING_KEY("color_fg", color_fg)
    STRING_KEY("color_dim", color_dim)
    STRING_KEY("color_val", color_val)
    STRING_KEY("color_sep", color_sep)
    STRING_KEY("color_alert", color_alert)
    STRING_KEY("color_warn", color_warn)
    STRING_KEY("color_peach", color_peach)
    STRING_KEY("color_info", color_info)
    BOOL_KEY("seg_thermal", seg_thermal)
    BOOL_KEY("seg_sleep_risk", seg_sleep_risk)
    BOOL_KEY("seg_disk", seg_disk)
    BOOL_KEY("seg_battery", seg_battery)
    BOOL_KEY("seg_cpu", seg_cpu)
    BOOL_KEY("seg_memory", seg_memory)
    BOOL_KEY("seg_multi_client", seg_multi_client)
    BOOL_KEY("seg_clock", seg_clock)
    STRING_KEY("glyph_sep", glyph_sep)
    STRING_KEY("glyph_thermal", glyph_thermal)
    STRING_KEY("glyph_sleep", glyph_sleep)
    STRING_KEY("glyph_disk", glyph_disk)
    STRING_KEY("glyph_battery_full", glyph_battery_full)
    STRING_KEY("glyph_battery_mid", glyph_battery_mid)
    STRING_KEY("glyph_battery_low", glyph_battery_low)
    STRING_KEY("glyph_cpu", glyph_cpu)
    STRING_KEY("glyph_memory", glyph_memory)
    STRING_KEY("glyph_clients", glyph_clients)
    INT_KEY("disk_warn_gb", disk_warn_gb, 0, 100000)
    INT_KEY("disk_crit_gb", disk_crit_gb, 0, 100000)
    INT_KEY("cpu_warn_pct", cpu_warn_pct, 0, 100)
    INT_KEY("cpu_crit_pct", cpu_crit_pct, 0, 100)
    INT_KEY("battery_warn_pct", battery_warn_pct, 0, 100)
    INT_KEY("battery_crit_pct", battery_crit_pct, 0, 100)
#undef STRING_KEY
#undef BOOL_KEY
#undef INT_KEY
}

static void load_config(struct config *c, const char *path)
{
    FILE *file;
    char line[514];
    config_defaults(c);
    file = fopen(path, "r");
    if (file == NULL) return;
    while (fgets(line, sizeof(line), file) != NULL) {
        size_t len = strlen(line);
        bool terminated = len > 0 && line[len - 1] == '\n';
        if (!terminated && !feof(file)) {
            int ch;
            while ((ch = fgetc(file)) != '\n' && ch != EOF) {}
            continue;
        }
        if (terminated) line[--len] = '\0';
        if (len > 0 && line[len - 1] == '\r') line[--len] = '\0';
        if (len > 512 || len == 0 || line[0] == '#') continue;
        char *equal = strchr(line, '=');
        if (equal == NULL || equal == line) continue;
        bool key_ok = true;
        for (char *p = line; p < equal; ++p) {
            if (!(*p == '_' || (*p >= 'a' && *p <= 'z') || isdigit((unsigned char)*p))) {
                key_ok = false;
                break;
            }
        }
        if (!key_ok) continue;
        *equal = '\0';
        parse_config_value(c, line, equal + 1);
    }
    (void)fclose(file);
}

static uint64_t monotonic_raw_ns(void)
{
    struct timespec ts;
#ifdef __APPLE__
    clockid_t clock_id = CLOCK_MONOTONIC_RAW;
#else
    clockid_t clock_id = CLOCK_MONOTONIC_RAW;
#endif
    if (clock_gettime(clock_id, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * NS_PER_SEC + (uint64_t)ts.tv_nsec;
}

static int secure_runtime_dir(char *path, size_t size)
{
    struct stat st;
    uid_t uid = geteuid();
#ifdef __APPLE__
    size_t needed = confstr(_CS_DARWIN_USER_TEMP_DIR, path, size);
    if (needed == 0 || needed > size) return -1;
#else
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime != NULL && *runtime != '\0' && strlen(runtime) < size) {
        copy_string(path, size, runtime);
    } else {
        if (snprintf(path, size, "/tmp/tmux-sentinel-%lu", (unsigned long)uid) >= (int)size) return -1;
        if (mkdir(path, 0700) != 0 && errno != EEXIST) return -1;
    }
#endif
    if (lstat(path, &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != uid || (st.st_mode & 0077) != 0) return -1;
    return 0;
}

static int open_secure_state(const char *name)
{
    char path[PATH_MAX];
    struct stat st;
    if (secure_runtime_dir(path, sizeof(path)) != 0) return -1;
    int dirfd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (dirfd < 0) return -1;
    int fd = openat(dirfd, name, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    close(dirfd);
    if (fd < 0) return -1;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != geteuid() ||
        (st.st_mode & 0077) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static bool read_cpu_sample(struct cpu_sample *sample)
{
#ifdef __APPLE__
    natural_t count = HOST_CPU_LOAD_INFO_COUNT;
    host_cpu_load_info_data_t info;
    mach_port_t host = mach_host_self();
    kern_return_t result = host_statistics64(host, HOST_CPU_LOAD_INFO, (host_info64_t)&info, &count);
    mach_port_deallocate(mach_task_self(), host);
    if (result != KERN_SUCCESS) return false;
    sample->user = info.cpu_ticks[CPU_STATE_USER];
    sample->system = info.cpu_ticks[CPU_STATE_SYSTEM];
    sample->idle = info.cpu_ticks[CPU_STATE_IDLE];
    sample->nice = info.cpu_ticks[CPU_STATE_NICE];
    return true;
#else
    FILE *file = fopen("/proc/stat", "r");
    char line[512];
    unsigned long long user, nice, system, idle, iowait, irq, softirq, steal;
    if (file == NULL) return false;
    if (fgets(line, sizeof(line), file) == NULL) { fclose(file); return false; }
    fclose(file);
    if (sscanf(line, "cpu %llu %llu %llu %llu %llu %llu %llu %llu", &user, &nice, &system,
               &idle, &iowait, &irq, &softirq, &steal) < 4) return false;
    sample->user = user;
    sample->nice = nice;
    sample->system = system + irq + softirq + steal;
    sample->idle = idle + iowait;
    return true;
#endif
}

static int cpu_percentage(const struct cpu_sample *old, const struct cpu_sample *cur)
{
    uint64_t du = cur->user >= old->user ? cur->user - old->user : 0;
    uint64_t ds = cur->system >= old->system ? cur->system - old->system : 0;
    uint64_t di = cur->idle >= old->idle ? cur->idle - old->idle : 0;
    uint64_t dn = cur->nice >= old->nice ? cur->nice - old->nice : 0;
    uint64_t total = du + ds + di + dn;
    uint64_t used = du + ds + dn;
    int result = total == 0 ? 0 : (int)((used * 100 + total / 2) / total);
    if (result < 0) result = 0;
    if (result > 100) result = 100;
    return result;
}

static bool probe_cpu(int *percentage)
{
    struct cpu_record record;
    struct cpu_sample current, later;
    uint64_t now, age;
    int fd = open_secure_state("tmux-sentinel-cpu.state");
    bool record_valid = false, ok = false;
    if (fd < 0) return false;
    if (flock(fd, LOCK_EX) != 0) goto done;
    ssize_t got = pread(fd, &record, sizeof(record), 0);
    record_valid = got == (ssize_t)sizeof(record) && record.magic == CPU_STATE_MAGIC &&
                   record.version == CPU_STATE_VERSION && record.percentage >= 0 && record.percentage <= 100;
    if (!read_cpu_sample(&current)) goto unlock;
    now = monotonic_raw_ns();
    age = record_valid && now >= record.timestamp_ns ? now - record.timestamp_ns : UINT64_MAX;
    if (record_valid && age < CPU_CACHE_NS) {
        *percentage = record.percentage;
        ok = true;
        goto unlock;
    }
    if (record_valid && age <= CPU_STALE_NS) {
        *percentage = cpu_percentage(&record.sample, &current);
    } else {
        struct cpu_sample first = current;
        struct timespec delay = { .tv_sec = 0, .tv_nsec = (long)CPU_SAMPLE_NS };
        while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}
        if (!read_cpu_sample(&later)) goto unlock;
        *percentage = cpu_percentage(&first, &later);
        current = later;
    }
    record.magic = CPU_STATE_MAGIC;
    record.version = CPU_STATE_VERSION;
    record.timestamp_ns = monotonic_raw_ns();
    record.sample = current;
    record.percentage = *percentage;
    record.reserved = 0;
    if (ftruncate(fd, 0) != 0 || pwrite(fd, &record, sizeof(record), 0) != (ssize_t)sizeof(record)) goto unlock;
    ok = true;
unlock:
    (void)flock(fd, LOCK_UN);
done:
    close(fd);
    return ok;
}

static bool probe_disk(int *free_gb)
{
#ifdef __APPLE__
    struct statfs fs;
    const char *path = access("/System/Volumes/Data", F_OK) == 0 ? "/System/Volumes/Data" : "/";
    if (statfs(path, &fs) != 0) return false;
    uint64_t bytes = (uint64_t)fs.f_bavail * (uint64_t)fs.f_bsize;
#else
    struct statvfs fs;
    if (statvfs("/", &fs) != 0) return false;
    uint64_t bytes = (uint64_t)fs.f_bavail * (uint64_t)fs.f_frsize;
#endif
    uint64_t gib = bytes / (UINT64_C(1024) * 1024 * 1024);
    *free_gb = gib > INT_MAX ? INT_MAX : (int)gib;
    return true;
}

#ifdef __APPLE__
static bool cfnumber_int(CFTypeRef value, int *result)
{
    return value != NULL && CFGetTypeID(value) == CFNumberGetTypeID() &&
           CFNumberGetValue((CFNumberRef)value, kCFNumberIntType, result);
}

static bool probe_memory(double *swap_gb, int *pressure)
{
    struct xsw_usage swap;
    size_t size = sizeof(swap);
    if (sysctlbyname("vm.swapusage", &swap, &size, NULL, 0) != 0 || size != sizeof(swap)) return false;
    *swap_gb = (double)swap.xsu_used / (1024.0 * 1024.0 * 1024.0);
    size = sizeof(*pressure);
    if (sysctlbyname("kern.memorystatus_vm_pressure_level", pressure, &size, NULL, 0) != 0) return false;
    return true;
}

static bool probe_battery(bool *present, bool *discharging, int *percentage)
{
    CFTypeRef info = IOPSCopyPowerSourcesInfo();
    if (info == NULL) return false;
    CFArrayRef list = IOPSCopyPowerSourcesList(info);
    if (list == NULL) { CFRelease(info); return false; }
    int64_t current_sum = 0, max_sum = 0;
    bool any_discharging = false;
    CFIndex count = CFArrayGetCount(list);
    for (CFIndex i = 0; i < count; ++i) {
        CFTypeRef source = CFArrayGetValueAtIndex(list, i);
        CFDictionaryRef desc = IOPSGetPowerSourceDescription(info, source);
        if (desc == NULL) continue;
        int current = 0, maximum = 0;
        if (!cfnumber_int(CFDictionaryGetValue(desc, CFSTR(kIOPSCurrentCapacityKey)), &current) ||
            !cfnumber_int(CFDictionaryGetValue(desc, CFSTR(kIOPSMaxCapacityKey)), &maximum) || maximum <= 0) continue;
        current_sum += current;
        max_sum += maximum;
        CFTypeRef state = CFDictionaryGetValue(desc, CFSTR(kIOPSPowerSourceStateKey));
        CFTypeRef charging = CFDictionaryGetValue(desc, CFSTR(kIOPSIsChargingKey));
        bool is_charging = charging == kCFBooleanTrue;
        if (state != NULL && CFGetTypeID(state) == CFStringGetTypeID() &&
            CFEqual(state, CFSTR(kIOPSBatteryPowerValue)) && !is_charging) any_discharging = true;
    }
    CFRelease(list);
    CFRelease(info);
    *present = max_sum > 0;
    *discharging = any_discharging;
    *percentage = max_sum > 0 ? (int)((current_sum * 100 + max_sum / 2) / max_sum) : 0;
    return true;
}

static bool dict_number(CFDictionaryRef dict, CFStringRef key, int *value)
{
    if (dict == NULL) return false;
    return cfnumber_int(CFDictionaryGetValue(dict, key), value);
}

static bool find_sleep_minutes(CFTypeRef object, int depth, int *minutes)
{
    if (object == NULL || depth > 4) return false;
    if (CFGetTypeID(object) != CFDictionaryGetTypeID()) return false;
    CFDictionaryRef dict = (CFDictionaryRef)object;
    int value;
    if (dict_number(dict, CFSTR("System Sleep Timer"), &value) || dict_number(dict, CFSTR("sleep"), &value)) {
        if (value >= 0 && value <= 1440) { *minutes = value; return true; }
    }
    CFIndex count = CFDictionaryGetCount(dict);
    const void **keys = calloc((size_t)count, sizeof(*keys));
    const void **values = calloc((size_t)count, sizeof(*values));
    if (keys == NULL || values == NULL) { free(keys); free(values); return false; }
    CFDictionaryGetKeysAndValues(dict, keys, values);
    bool found = false;
    for (CFIndex i = 0; i < count && !found; ++i) found = find_sleep_minutes(values[i], depth + 1, minutes);
    free(keys);
    free(values);
    return found;
}

static bool assertion_held(CFDictionaryRef assertions, CFStringRef key)
{
    int level = 0;
    return dict_number(assertions, key, &level) && level > 0;
}

static bool probe_sleep(bool *risk, int *minutes)
{
    CFDictionaryRef preferences = IOPMCopySystemPowerSettings();
    bool found = preferences != NULL && find_sleep_minutes(preferences, 0, minutes);
    if (preferences != NULL) CFRelease(preferences);
    if (!found) {
        preferences = IOPMCopyPMPreferences();
        found = preferences != NULL && find_sleep_minutes(preferences, 0, minutes);
        if (preferences != NULL) CFRelease(preferences);
    }
    if (!found) return false;
    CFDictionaryRef assertions = NULL;
    if (IOPMCopyAssertionsStatus(&assertions) != kIOReturnSuccess || assertions == NULL) return false;
    bool held = assertion_held(assertions, kIOPMAssertionTypePreventUserIdleSystemSleep) ||
                assertion_held(assertions, kIOPMAssertionTypePreventSystemSleep);
    CFRelease(assertions);
    *risk = *minutes > 0 && !held;
    return true;
}

static bool probe_thermal(bool *alert, int *level, char *word, size_t word_size)
{
    int token = 0;
    uint64_t state = 0;
    if (notify_register_check(kOSThermalNotificationPressureLevelName, &token) != NOTIFY_STATUS_OK) return false;
    uint32_t status = notify_get_state(token, &state);
    (void)notify_cancel(token);
    if (status != NOTIFY_STATUS_OK || state > INT_MAX) return false;
    *level = (int)state;
    *alert = state > kOSThermalPressureLevelNominal;
    if (state == kOSThermalPressureLevelNominal) copy_string(word, word_size, "Nominal");
    else if (state == kOSThermalPressureLevelModerate) copy_string(word, word_size, "Fair");
    else if (state == kOSThermalPressureLevelHeavy) copy_string(word, word_size, "Serious");
    else copy_string(word, word_size, "Critical");
    return true;
}

#define PLATFORM_CACHE_MAGIC UINT32_C(0x534e5043)
#define PLATFORM_CACHE_VERSION 1U
#define PLATFORM_CACHE_NS (UINT64_C(5) * NS_PER_SEC)

struct platform_cache {
    uint32_t magic, version;
    uint64_t timestamp_ns;
    int32_t thermal_ok, thermal_alert, thermal_value;
    char thermal_word[16];
    int32_t sleep_ok, sleep_risk, sleep_minutes;
};

static void probe_cached_platform(struct metrics *m)
{
    struct platform_cache cache;
    int fd = open_secure_state("tmux-sentinel-platform.state");
    if (fd < 0 || flock(fd, LOCK_EX) != 0) {
        if (fd >= 0) close(fd);
        m->thermal_ok = probe_thermal(&m->thermal_alert, &m->thermal_value,
                                      m->thermal_word, sizeof(m->thermal_word));
        m->sleep_ok = probe_sleep(&m->sleep_risk, &m->sleep_minutes);
        return;
    }
    ssize_t got = pread(fd, &cache, sizeof(cache), 0);
    uint64_t now = monotonic_raw_ns();
    bool valid = got == (ssize_t)sizeof(cache) && cache.magic == PLATFORM_CACHE_MAGIC &&
                 cache.version == PLATFORM_CACHE_VERSION && now >= cache.timestamp_ns &&
                 now - cache.timestamp_ns < PLATFORM_CACHE_NS;
    if (valid) {
        m->thermal_ok = cache.thermal_ok != 0;
        m->thermal_alert = cache.thermal_alert != 0;
        m->thermal_value = cache.thermal_value;
        copy_string(m->thermal_word, sizeof(m->thermal_word), cache.thermal_word);
        m->sleep_ok = cache.sleep_ok != 0;
        m->sleep_risk = cache.sleep_risk != 0;
        m->sleep_minutes = cache.sleep_minutes;
    } else {
        m->thermal_ok = probe_thermal(&m->thermal_alert, &m->thermal_value,
                                      m->thermal_word, sizeof(m->thermal_word));
        m->sleep_ok = probe_sleep(&m->sleep_risk, &m->sleep_minutes);
        memset(&cache, 0, sizeof(cache));
        cache.magic = PLATFORM_CACHE_MAGIC;
        cache.version = PLATFORM_CACHE_VERSION;
        cache.timestamp_ns = monotonic_raw_ns();
        cache.thermal_ok = m->thermal_ok;
        cache.thermal_alert = m->thermal_alert;
        cache.thermal_value = m->thermal_value;
        copy_string(cache.thermal_word, sizeof(cache.thermal_word), m->thermal_word);
        cache.sleep_ok = m->sleep_ok;
        cache.sleep_risk = m->sleep_risk;
        cache.sleep_minutes = m->sleep_minutes;
        if (ftruncate(fd, 0) == 0)
            (void)pwrite(fd, &cache, sizeof(cache), 0);
    }
    (void)flock(fd, LOCK_UN);
    close(fd);
}
#else
static bool read_u64_file(const char *path, uint64_t *value)
{
    FILE *file = fopen(path, "r");
    unsigned long long parsed;
    if (file == NULL) return false;
    bool ok = fscanf(file, "%llu", &parsed) == 1;
    fclose(file);
    if (ok) *value = parsed;
    return ok;
}

static bool probe_memory(double *swap_gb, int *pressure)
{
    FILE *file = fopen("/proc/meminfo", "r");
    char key[64], unit[16];
    unsigned long long value, total = 0, available = 0, swap_total = 0, swap_free = 0;
    if (file == NULL) return false;
    while (fscanf(file, "%63s %llu %15s", key, &value, unit) == 3) {
        if (strcmp(key, "MemTotal:") == 0) total = value;
        else if (strcmp(key, "MemAvailable:") == 0) available = value;
        else if (strcmp(key, "SwapTotal:") == 0) swap_total = value;
        else if (strcmp(key, "SwapFree:") == 0) swap_free = value;
    }
    fclose(file);
    if (total == 0 || swap_free > swap_total) return false;
    *swap_gb = (double)(swap_total - swap_free) / (1024.0 * 1024.0);
    double ratio = (double)available / (double)total;
    *pressure = ratio <= 0.10 ? 4 : ratio <= 0.20 ? 2 : 1;
    return true;
}

static bool read_text_file(const char *path, char *value, size_t size)
{
    FILE *file = fopen(path, "r");
    if (file == NULL) return false;
    if (fgets(value, (int)size, file) == NULL) { fclose(file); return false; }
    fclose(file);
    value[strcspn(value, "\r\n")] = '\0';
    return true;
}

static bool probe_battery(bool *present, bool *discharging, int *percentage)
{
    DIR *dir = opendir("/sys/class/power_supply");
    struct dirent *entry;
    uint64_t now_sum = 0, full_sum = 0;
    bool any = false, any_discharging = false;
    if (dir == NULL) return false;
    while ((entry = readdir(dir)) != NULL) {
        char base[PATH_MAX], path[PATH_MAX], type[64], status[64];
        uint64_t now = 0, full = 0, capacity = 0;
        if (entry->d_name[0] == '.') continue;
        if (snprintf(base, sizeof(base), "/sys/class/power_supply/%s", entry->d_name) >= (int)sizeof(base)) continue;
        snprintf(path, sizeof(path), "%s/type", base);
        if (!read_text_file(path, type, sizeof(type)) || strcmp(type, "Battery") != 0) continue;
        any = true;
        snprintf(path, sizeof(path), "%s/status", base);
        if (read_text_file(path, status, sizeof(status)) && strcmp(status, "Discharging") == 0) any_discharging = true;
        snprintf(path, sizeof(path), "%s/energy_now", base);
        bool have_now = read_u64_file(path, &now);
        snprintf(path, sizeof(path), "%s/energy_full", base);
        bool have_full = read_u64_file(path, &full);
        if (!have_now || !have_full || full == 0) {
            snprintf(path, sizeof(path), "%s/charge_now", base);
            have_now = read_u64_file(path, &now);
            snprintf(path, sizeof(path), "%s/charge_full", base);
            have_full = read_u64_file(path, &full);
        }
        if (have_now && have_full && full > 0) { now_sum += now; full_sum += full; }
        else {
            snprintf(path, sizeof(path), "%s/capacity", base);
            if (read_u64_file(path, &capacity) && capacity <= 100) { now_sum += capacity; full_sum += 100; }
        }
    }
    closedir(dir);
    *present = any && full_sum > 0;
    *discharging = any_discharging;
    *percentage = full_sum > 0 ? (int)((now_sum * 100 + full_sum / 2) / full_sum) : 0;
    return true;
}

static bool thermal_type_cpu(const char *type)
{
    char lowered[128];
    size_t n = strlen(type);
    if (n >= sizeof(lowered)) n = sizeof(lowered) - 1;
    for (size_t i = 0; i < n; ++i) lowered[i] = (char)tolower((unsigned char)type[i]);
    lowered[n] = '\0';
    return strstr(lowered, "cpu") != NULL || strstr(lowered, "package") != NULL ||
           strstr(lowered, "soc") != NULL || strstr(lowered, "coretemp") != NULL ||
           strstr(lowered, "k10temp") != NULL;
}

static void scan_thermal_zones(int *hottest, bool *found)
{
    DIR *dir = opendir("/sys/class/thermal");
    struct dirent *entry;
    if (dir == NULL) return;
    while ((entry = readdir(dir)) != NULL) {
        char base[PATH_MAX], path[PATH_MAX], type[128];
        uint64_t temp;
        if (strncmp(entry->d_name, "thermal_zone", 12) != 0) continue;
        if (snprintf(base, sizeof(base), "/sys/class/thermal/%s", entry->d_name) >= (int)sizeof(base)) continue;
        snprintf(path, sizeof(path), "%s/type", base);
        if (!read_text_file(path, type, sizeof(type)) || !thermal_type_cpu(type)) continue;
        snprintf(path, sizeof(path), "%s/temp", base);
        if (read_u64_file(path, &temp)) {
            int celsius = temp > 1000 ? (int)(temp / 1000) : (int)temp;
            if (!*found || celsius > *hottest) *hottest = celsius;
            *found = true;
        }
    }
    closedir(dir);
}

static void scan_hwmon(const char *root, int *hottest, bool *found)
{
    DIR *dir = opendir(root);
    struct dirent *entry;
    char path[PATH_MAX], device_name[128] = "";
    snprintf(path, sizeof(path), "%s/name", root);
    bool device_is_cpu = read_text_file(path, device_name, sizeof(device_name)) && thermal_type_cpu(device_name);
    if (dir == NULL) return;
    while ((entry = readdir(dir)) != NULL) {
        size_t len = strlen(entry->d_name);
        const char suffix[] = "_input";
        if (strncmp(entry->d_name, "temp", 4) != 0 || len <= sizeof(suffix) - 1 ||
            strcmp(entry->d_name + len - (sizeof(suffix) - 1), suffix) != 0) continue;
        bool sensor_is_cpu = device_is_cpu;
        char label_name[NAME_MAX + 1], label[128];
        size_t stem = len - (sizeof(suffix) - 1);
        if (stem + strlen("_label") < sizeof(label_name)) {
            memcpy(label_name, entry->d_name, stem);
            copy_string(label_name + stem, sizeof(label_name) - stem, "_label");
            snprintf(path, sizeof(path), "%s/%s", root, label_name);
            if (read_text_file(path, label, sizeof(label)) && thermal_type_cpu(label)) sensor_is_cpu = true;
        }
        if (!sensor_is_cpu) continue;
        uint64_t temp;
        snprintf(path, sizeof(path), "%s/%s", root, entry->d_name);
        if (read_u64_file(path, &temp)) {
            int celsius = temp > 1000 ? (int)(temp / 1000) : (int)temp;
            if (!*found || celsius > *hottest) *hottest = celsius;
            *found = true;
        }
    }
    closedir(dir);
}

static bool probe_thermal(bool *alert, int *temperature, char *word, size_t word_size)
{
    bool found = false;
    *temperature = 0;
    scan_thermal_zones(temperature, &found);
    if (!found) {
        DIR *hwmons = opendir("/sys/class/hwmon");
        struct dirent *entry;
        if (hwmons != NULL) {
            while ((entry = readdir(hwmons)) != NULL) {
                char root[PATH_MAX];
                if (strncmp(entry->d_name, "hwmon", 5) != 0) continue;
                snprintf(root, sizeof(root), "/sys/class/hwmon/%s", entry->d_name);
                scan_hwmon(root, temperature, &found);
            }
            closedir(hwmons);
        }
    }
    word[0] = '\0';
    (void)word_size;
    *alert = found && *temperature >= 80;
    return found;
}
#endif

static bool probe_clock(char *clock, size_t size, const char *format)
{
    time_t now = time(NULL);
    struct tm local;
    return now != (time_t)-1 && localtime_r(&now, &local) != NULL && strftime(clock, size, format, &local) > 0;
}

static void collect_metrics(struct metrics *m, int clients, const struct config *config, bool all)
{
    memset(m, 0, sizeof(*m));
    m->clients = clients;
#ifdef __APPLE__
    if (all) {
        m->thermal_ok = probe_thermal(&m->thermal_alert, &m->thermal_value,
                                      m->thermal_word, sizeof(m->thermal_word));
        m->sleep_ok = probe_sleep(&m->sleep_risk, &m->sleep_minutes);
    } else if (config->seg_thermal || config->seg_sleep_risk) {
        probe_cached_platform(m);
    }
#else
    if (all || config->seg_thermal)
        m->thermal_ok = probe_thermal(&m->thermal_alert, &m->thermal_value,
                                      m->thermal_word, sizeof(m->thermal_word));
    m->sleep_ok = true;
    m->sleep_risk = false;
#endif
    if (all || config->seg_disk) m->disk_ok = probe_disk(&m->disk_free_gb);
    if (all || config->seg_battery)
        m->battery_ok = probe_battery(&m->battery_present, &m->battery_discharging, &m->battery_pct);
    if (all || config->seg_cpu) m->cpu_ok = probe_cpu(&m->cpu_pct);
    if (all || config->seg_memory) m->memory_ok = probe_memory(&m->swap_gb, &m->memory_pressure);
    if (all || config->seg_clock) m->clock_ok = probe_clock(m->clock, sizeof(m->clock), config->clock_format);
}

static void simulate_metrics(struct metrics *m, bool alert)
{
    memset(m, 0, sizeof(*m));
    m->thermal_ok = m->sleep_ok = m->disk_ok = m->battery_ok = true;
    m->cpu_ok = m->memory_ok = m->clock_ok = true;
    m->thermal_alert = alert;
    m->thermal_value = alert ? 1 : 0;
    copy_string(m->thermal_word, sizeof(m->thermal_word), alert ? "Fair" : "Nominal");
    m->sleep_risk = alert;
    m->sleep_minutes = alert ? 10 : 0;
    m->disk_free_gb = alert ? 12 : 54;
    m->battery_present = true;
    m->battery_discharging = alert;
    m->battery_pct = alert ? 18 : 95;
    m->cpu_pct = alert ? 94 : 22;
    m->swap_gb = alert ? 24.1 : 23.3;
    m->memory_pressure = alert ? 4 : 1;
    m->clients = alert ? 2 : 1;
    copy_string(m->clock, sizeof(m->clock), "14:30");
}

static void appendf(struct outbuf *out, const char *format, ...)
{
    va_list args;
    if (out->truncated) return;
    va_start(args, format);
    int written = vsnprintf(out->data + out->len, sizeof(out->data) - out->len, format, args);
    va_end(args);
    if (written < 0 || (size_t)written >= sizeof(out->data) - out->len) {
        out->truncated = true;
        out->data[sizeof(out->data) - 1] = '\0';
        return;
    }
    out->len += (size_t)written;
}

static void join_segment(struct outbuf *out, const struct config *c, const char *segment)
{
    if (out->len > 0) appendf(out, "#[fg=%s]%s", c->color_sep, c->glyph_sep);
    appendf(out, "%s", segment);
}

static void render(const struct config *c, const struct metrics *m, struct outbuf *out)
{
    char segment[512];
    memset(out, 0, sizeof(*out));
    if (c->seg_thermal && m->thermal_ok && (m->thermal_alert || !c->alerts_only)) {
        if (m->thermal_word[0] != '\0') {
            if (m->thermal_alert) snprintf(segment, sizeof(segment), "#[fg=%s]%s %s", c->color_alert, c->glyph_thermal, m->thermal_word);
            else snprintf(segment, sizeof(segment), "#[fg=%s]%s #[fg=%s]%s", c->color_dim, c->glyph_thermal, c->color_val, m->thermal_word);
        } else {
            const char *color = m->thermal_alert ? c->color_alert : c->color_val;
            snprintf(segment, sizeof(segment), "#[fg=%s]%s %d°C", color, c->glyph_thermal, m->thermal_value);
        }
        join_segment(out, c, segment);
    }
    if (c->seg_sleep_risk && m->sleep_ok && m->sleep_risk) {
        snprintf(segment, sizeof(segment), "#[fg=%s]%s %dm", c->color_alert, c->glyph_sleep, m->sleep_minutes);
        join_segment(out, c, segment);
    }
    if (c->seg_disk && m->disk_ok && (!c->alerts_only || m->disk_free_gb < c->disk_warn_gb)) {
        const char *color = c->color_val;
        if (m->disk_free_gb < c->disk_crit_gb) color = c->color_alert;
        else if (m->disk_free_gb < c->disk_warn_gb) color = c->color_warn;
        snprintf(segment, sizeof(segment), "#[fg=%s]%s #[fg=%s]%dG", c->color_dim, c->glyph_disk, color, m->disk_free_gb);
        join_segment(out, c, segment);
    }
    if (c->seg_battery && m->battery_ok && m->battery_present && (m->battery_discharging || !c->alerts_only)) {
        if (!m->battery_discharging) {
            snprintf(segment, sizeof(segment), "#[fg=%s]%s #[fg=%s]%d%%", c->color_dim, c->glyph_battery_full, c->color_val, m->battery_pct);
        } else {
            const char *color = c->color_warn;
            const char *glyph = c->glyph_battery_full;
            if (m->battery_pct < c->battery_crit_pct) { color = c->color_alert; glyph = c->glyph_battery_low; }
            else if (m->battery_pct < c->battery_warn_pct) { color = c->color_peach; glyph = c->glyph_battery_mid; }
            snprintf(segment, sizeof(segment), "#[fg=%s]%s %d%%", color, glyph, m->battery_pct);
        }
        join_segment(out, c, segment);
    }
    if (c->seg_cpu && m->cpu_ok) {
        const char *color = c->color_val;
        if (m->cpu_pct >= c->cpu_crit_pct) color = c->color_alert;
        else if (m->cpu_pct >= c->cpu_warn_pct) color = c->color_peach;
        snprintf(segment, sizeof(segment), "#[fg=%s]%s #[fg=%s]%2d%%", c->color_dim, c->glyph_cpu, color, m->cpu_pct);
        join_segment(out, c, segment);
    }
    if (c->seg_memory && m->memory_ok) {
        const char *color = c->color_val;
        if (m->memory_pressure >= 4) color = c->color_alert;
        else if (m->memory_pressure >= 2) color = c->color_warn;
        snprintf(segment, sizeof(segment), "#[fg=%s]%s #[fg=%s]%.1fG", c->color_dim, c->glyph_memory, color, m->swap_gb);
        join_segment(out, c, segment);
    }
    if (c->seg_multi_client && m->clients > 1) {
        snprintf(segment, sizeof(segment), "#[fg=%s]%s %d", c->color_info, c->glyph_clients, m->clients);
        join_segment(out, c, segment);
    }
    if (c->seg_clock && m->clock_ok) {
        snprintf(segment, sizeof(segment), "#[fg=%s,bold]%s", c->color_fg, m->clock);
        join_segment(out, c, segment);
    }
}

static void selftest_line(const char *name, const char *value, bool ok)
{
    printf("%s: %s | %s\n", name, value, ok ? "ok" : "failed");
}

static int run_selftest(const struct metrics *m)
{
    char value[128];
    bool all_ok = m->thermal_ok && m->sleep_ok && m->disk_ok && m->battery_ok &&
                  m->cpu_ok && m->memory_ok && m->clock_ok;
#ifdef __APPLE__
    snprintf(value, sizeof(value), "%s", m->thermal_ok ? m->thermal_word : "unavailable");
#else
    snprintf(value, sizeof(value), m->thermal_ok ? "%d°C" : "unavailable", m->thermal_value);
#endif
    selftest_line("thermal", value, m->thermal_ok);
#ifdef __APPLE__
    snprintf(value, sizeof(value), m->sleep_ok ? "%dm%s" : "unavailable", m->sleep_minutes, m->sleep_risk ? " risk" : " safe");
#else
    snprintf(value, sizeof(value), "not implemented on Linux");
#endif
    selftest_line("sleep_risk", value, m->sleep_ok);
    snprintf(value, sizeof(value), m->disk_ok ? "%dG free" : "unavailable", m->disk_free_gb);
    selftest_line("disk", value, m->disk_ok);
    if (m->battery_ok && m->battery_present) snprintf(value, sizeof(value), "%d%% %s", m->battery_pct, m->battery_discharging ? "discharging" : "charging/AC");
    else snprintf(value, sizeof(value), m->battery_ok ? "not present" : "unavailable");
    selftest_line("battery", value, m->battery_ok);
    snprintf(value, sizeof(value), m->cpu_ok ? "%d%%" : "unavailable", m->cpu_pct);
    selftest_line("cpu", value, m->cpu_ok);
    snprintf(value, sizeof(value), m->memory_ok ? "%.1fG swap, pressure %d" : "unavailable", m->swap_gb, m->memory_pressure);
    selftest_line("memory", value, m->memory_ok);
    snprintf(value, sizeof(value), "%d", m->clients);
    selftest_line("multi_client", value, true);
    snprintf(value, sizeof(value), m->clock_ok ? "%s" : "unavailable", m->clock);
    selftest_line("clock", value, m->clock_ok);
    return all_ok ? 0 : 1;
}

static void usage(FILE *stream)
{
    fprintf(stream, "usage: sentinel-status [--state PATH] [--clients N] [--simulate healthy|alert] [--selftest] [--version]\n");
}

static const char *default_state_path(char *path, size_t size)
{
    const char *base = getenv("XDG_CONFIG_HOME");
    char fallback[PATH_MAX];
    if (base == NULL || *base == '\0') {
        const char *home = getenv("HOME");
        if (home == NULL || *home == '\0') home = ".";
        if (snprintf(fallback, sizeof(fallback), "%s/.config", home) >= (int)sizeof(fallback)) fallback[0] = '\0';
        base = fallback;
    }
    if (snprintf(path, size, "%s/tmux-sentinel/sentinel.state", base) >= (int)size) path[0] = '\0';
    return path;
}

int main(int argc, char **argv)
{
    char state_storage[PATH_MAX];
    const char *state_path = default_state_path(state_storage, sizeof(state_storage));
    const char *simulate = NULL;
    int clients = 1;
    bool selftest = false;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--state") == 0 && i + 1 < argc) state_path = argv[++i];
        else if (strcmp(argv[i], "--clients") == 0 && i + 1 < argc) {
            int parsed;
            if (parse_integer(argv[++i], 0, INT_MAX, &parsed)) clients = parsed;
            else clients = 1;
        } else if (strcmp(argv[i], "--simulate") == 0 && i + 1 < argc) {
            simulate = argv[++i];
            if (strcmp(simulate, "healthy") != 0 && strcmp(simulate, "alert") != 0) { usage(stderr); return 2; }
        } else if (strcmp(argv[i], "--selftest") == 0) selftest = true;
        else if (strcmp(argv[i], "--version") == 0) { printf("sentinel-status %s\n", VERSION); return 0; }
        else { usage(stderr); return 2; }
    }
    struct config config;
    struct metrics metrics;
    load_config(&config, state_path);
    if (simulate != NULL) simulate_metrics(&metrics, strcmp(simulate, "alert") == 0);
    else collect_metrics(&metrics, clients, &config, selftest);
    if (selftest) return run_selftest(&metrics);
    struct outbuf output;
    render(&config, &metrics, &output);
    if (output.truncated) return 1;
    (void)fwrite(output.data, 1, output.len, stdout);
    return 0;
}
