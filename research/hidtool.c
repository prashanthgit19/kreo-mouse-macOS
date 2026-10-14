// hidtool — minimal Hawk Pro HID config transport for macOS (IOKit, no deps).
//
// Build: clang -O2 -framework IOKit -framework CoreFoundation hidtool.c -o hidtool
//
// Usage:
//   hidtool list                 list matching devices + interfaces
//   hidtool descriptor           hex dump of the config node report descriptor
//   hidtool get <rid> <len>      read feature report -> prints hex line
//   hidtool set <rid> <hex...>   write feature report
//   hidtool server               line protocol on stdin/stdout (for the oracle bridge):
//                                  GET <rid> <len>   -> OK <hex>
//                                  SET <rid> <hex>   -> OK
//                                  PING              -> PONG
//                                  QUIT              -> BYE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <IOKit/hid/IOHIDManager.h>
#include <IOKit/hid/IOHIDKeys.h>

#define KREO_VID 0x04F3
#define KREO_PID_WIRED 0x026E
#define KREO_PID_DONGLE 0x026F

static int get_int(IOHIDDeviceRef d, CFStringRef key) {
  CFTypeRef v = IOHIDDeviceGetProperty(d, key);
  int out = -1;
  if (v && CFGetTypeID(v) == CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v, kCFNumberIntType, &out);
  return out;
}

// Find the config node (largest MaxFeatureReportSize) for a Kreo mouse.
static IOHIDDeviceRef find_config_node(IOHIDManagerRef mgr) {
  CFSetRef set = IOHIDManagerCopyDevices(mgr);
  if (!set) return NULL;
  CFIndex n = CFSetGetCount(set);
  const void **devs = malloc(sizeof(void*) * n);
  CFSetGetValues(set, devs);
  IOHIDDeviceRef best = NULL;
  int bestFeat = 0;
  for (CFIndex i = 0; i < n; i++) {
    IOHIDDeviceRef d = (IOHIDDeviceRef)devs[i];
    int f = get_int(d, CFSTR(kIOHIDMaxFeatureReportSizeKey));
    if (f > bestFeat) { bestFeat = f; best = d; }
  }
  free(devs);
  return best;
}

static IOHIDManagerRef make_manager(int open_it) {
  IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
  int vids[] = {KREO_VID}; int pids[] = {KREO_PID_WIRED, KREO_PID_DONGLE};
  CFMutableArrayRef matches = CFArrayCreateMutable(NULL, 0, &kCFTypeArrayCallBacks);
  for (int i = 0; i < 2; i++) {
    int vid = vids[0], pid = pids[i];
    CFNumberRef v = CFNumberCreate(NULL, kCFNumberIntType, &vid);
    CFNumberRef p = CFNumberCreate(NULL, kCFNumberIntType, &pid);
    const void *keys[] = { CFSTR(kIOHIDVendorIDKey), CFSTR(kIOHIDProductIDKey) };
    const void *vals[] = { v, p };
    CFDictionaryRef m = CFDictionaryCreate(NULL, keys, vals, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFArrayAppendValue(matches, m);
  }
  IOHIDManagerSetDeviceMatchingMultiple(mgr, matches);
  if (open_it) IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);
  return mgr;
}

static int hex_to_bytes(const char *hex, unsigned char *out, int max) {
  int n = 0;
  while (*hex && n < max) {
    while (*hex == ' ' || *hex == ',') hex++;
    if (!isxdigit((unsigned char)hex[0]) || !isxdigit((unsigned char)hex[1])) break;
    char b[3] = { hex[0], hex[1], 0 };
    out[n++] = (unsigned char)strtol(b, NULL, 16);
    hex += 2;
  }
  return n;
}

static void print_hex(const unsigned char *b, int n) {
  for (int i = 0; i < n; i++) printf("%02X%s", b[i], i + 1 < n ? " " : "");
}

// ---- input report watcher -------------------------------------------------
static volatile int g_reports = 0;
static void input_report_cb(void *ctx, IOReturn result, void *sender,
                            IOHIDReportType type, uint32_t reportID,
                            uint8_t *report, CFIndex reportLength) {
  (void)ctx; (void)result; (void)sender; (void)type;
  printf("IN id=%u len=%ld: ", reportID, (long)reportLength);
  print_hex(report, (int)reportLength);
  printf("\n");
  fflush(stdout);
  g_reports++;
}

static int cmd_watch(int seconds, int fire) {
  IOHIDManagerRef mgr = make_manager(1);
  IOHIDDeviceRef d = find_config_node(mgr);
  if (!d) { fprintf(stderr, "config node not found\n"); return 1; }
  IOReturn r = IOHIDDeviceOpen(d, kIOHIDOptionsTypeNone);
  if (r != kIOReturnSuccess) { fprintf(stderr, "open failed 0x%08X\n", r); return 1; }
  static uint8_t buf[4096];
  IOHIDDeviceRegisterInputReportCallback(d, buf, sizeof buf, input_report_cb, NULL);
  IOHIDDeviceScheduleWithRunLoop(d, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
  printf("watching %d s (input reports on config node)...\n", seconds);
  fflush(stdout);

  if (fire) {
    // send a harmless 'get LOD' style probe so the device has reason to answer
    unsigned char probe[31];
    memset(probe, 0xFF, sizeof probe);
    probe[0] = 0x05; probe[1] = 0x01; probe[2] = 0x01;
    probe[3] = (0x06 + 0x05 + 0x01 + 0x01 + 31 * 0xFF) & 0xFF;
    IOHIDDeviceSetReport(d, kIOHIDReportTypeFeature, 6, probe, 31);
  }

  CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds, false);
  printf("done, %d input reports seen\n", g_reports);
  IOHIDDeviceUnscheduleFromRunLoop(d, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
  return 0;
}

static int cmd_sendwatch(int rid, const unsigned char *data, int n, int seconds) {
  IOHIDManagerRef mgr = make_manager(1);
  IOHIDDeviceRef d = find_config_node(mgr);
  if (!d) { fprintf(stderr, "config node not found\n"); return 1; }
  if (IOHIDDeviceOpen(d, kIOHIDOptionsTypeNone) != kIOReturnSuccess) { fprintf(stderr, "open failed\n"); return 1; }
  static uint8_t buf[4096];
  IOHIDDeviceRegisterInputReportCallback(d, buf, sizeof buf, input_report_cb, NULL);
  IOHIDDeviceScheduleWithRunLoop(d, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
  unsigned char send[600];
  send[0] = (unsigned char)rid;
  if (n > 0 && (int)n < (int)sizeof(send) - 1) memcpy(send + 1, data, n);
  IOReturn r = IOHIDDeviceSetReport(d, kIOHIDReportTypeFeature, rid, send, n + 1);
  printf("sent id=%d -> 0x%08X\n", rid, r);
  fflush(stdout);
  CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds, false);
  printf("done, %d input reports seen\n", g_reports);
  return 0;
}

static void cmd_list(void) {  IOHIDManagerRef mgr = make_manager(0);
  IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);
  CFSetRef set = IOHIDManagerCopyDevices(mgr);
  CFIndex n = set ? CFSetGetCount(set) : 0;
  const void **devs = n ? malloc(sizeof(void*) * n) : NULL;
  if (n) CFSetGetValues(set, devs);
  printf("%ld matching HID device nodes\n", (long)n);
  for (CFIndex i = 0; i < n; i++) {
    IOHIDDeviceRef d = (IOHIDDeviceRef)devs[i];
    printf("  primary=0x%04X:0x%04X  maxFeatureBits=%d maxInputBits=%d maxOutputBits=%d\n",
      get_int(d, CFSTR(kIOHIDPrimaryUsagePageKey)), get_int(d, CFSTR(kIOHIDPrimaryUsageKey)),
      get_int(d, CFSTR(kIOHIDMaxFeatureReportSizeKey)),
      get_int(d, CFSTR(kIOHIDMaxInputReportSizeKey)),
      get_int(d, CFSTR(kIOHIDMaxOutputReportSizeKey)));
  }
}

static int cmd_descriptor(void) {
  IOHIDManagerRef mgr = make_manager(1);
  IOHIDDeviceRef d = find_config_node(mgr);
  if (!d) { printf("config node not found\n"); return 1; }
  CFTypeRef v = IOHIDDeviceGetProperty(d, CFSTR(kIOHIDReportDescriptorKey));
  if (!v || CFGetTypeID(v) != CFDataGetTypeID()) { printf("no report descriptor\n"); return 1; }
  CFDataRef data = (CFDataRef)v;
  const unsigned char *p = CFDataGetBytePtr(data);
  print_hex(p, (int)CFDataGetLength(data));
  printf("\n");
  return 0;
}

static int open_config(IOHIDDeviceRef *out) {
  IOHIDManagerRef mgr = make_manager(1);
  IOHIDDeviceRef d = find_config_node(mgr);
  if (!d) { fprintf(stderr, "config node not found\n"); return 1; }
  IOReturn r = IOHIDDeviceOpen(d, kIOHIDOptionsTypeNone);
  if (r != kIOReturnSuccess) { fprintf(stderr, "open failed 0x%08X\n", r); return 1; }
  *out = d;
  return 0;
}

static int cmd_get(int rid, int len) {
  IOHIDDeviceRef d;
  if (open_config(&d)) return 1;
  unsigned char buf[512];
  memset(buf, 0, sizeof buf);
  CFIndex nbytes = len;
  IOReturn r = IOHIDDeviceGetReport(d, kIOHIDReportTypeFeature, rid, buf, &nbytes);
  if (r != kIOReturnSuccess) { fprintf(stderr, "get failed 0x%08X\n", r); return 1; }
  print_hex(buf, (int)nbytes);
  printf("\n");
  return 0;
}

static int cmd_set(int rid, const unsigned char *data, int n) {
  IOHIDDeviceRef d;
  if (open_config(&d)) return 1;
  // Apple IOHIDDeviceSetReport requires the report ID as byte 0 of the buffer.
  unsigned char buf[600];
  buf[0] = (unsigned char)rid;
  memcpy(buf + 1, data, n);
  IOReturn r = IOHIDDeviceSetReport(d, kIOHIDReportTypeFeature, rid, buf, n + 1);
  if (r != kIOReturnSuccess) { fprintf(stderr, "set failed 0x%08X\n", r); return 1; }
  printf("OK\n");
  return 0;
}

static int cmd_server(void) {
  IOHIDDeviceRef d;
  if (open_config(&d)) return 1;
  printf("READY\n");
  fflush(stdout);
  char line[4096];
  while (fgets(line, sizeof line, stdin)) {
    char op[16] = {0};
    int rid = 0, len = 0;
    char hex[2048] = {0};
    if (sscanf(line, "%15s", op) != 1) continue;
    if (strcmp(op, "QUIT") == 0) { printf("BYE\n"); fflush(stdout); return 0; }
    if (strcmp(op, "PING") == 0) { printf("PONG\n"); fflush(stdout); continue; }
    if (strcmp(op, "GET") == 0) {
      sscanf(line, "%*s %d %d", &rid, &len);
      unsigned char buf[600]; memset(buf, 0, sizeof buf);
      CFIndex nbytes = len;
      IOReturn r = IOHIDDeviceGetReport(d, kIOHIDReportTypeFeature, rid, buf, &nbytes);
      if (r == kIOReturnSuccess && nbytes > 1) {
        // strip the leading report ID so callers get WebHID-style report data
        printf("OK "); print_hex(buf + 1, (int)nbytes - 1); printf("\n");
      } else if (r == kIOReturnSuccess) {
        printf("OK\n");
      } else printf("ERR 0x%08X\n", r);
      fflush(stdout);
    } else if (strcmp(op, "SET") == 0) {
      sscanf(line, "%*s %d %2047s", &rid, hex);
      unsigned char data[512];
      int n = hex_to_bytes(hex, data, sizeof data);
      unsigned char buf[600];
      buf[0] = (unsigned char)rid;
      memcpy(buf + 1, data, n);
      IOReturn r = IOHIDDeviceSetReport(d, kIOHIDReportTypeFeature, rid, buf, n + 1);
      if (r == kIOReturnSuccess) printf("OK\n"); else printf("ERR 0x%08X\n", r);
      fflush(stdout);
    } else {
      printf("ERR unknown-op\n"); fflush(stdout);
    }
  }
  return 0;
}

int main(int argc, char **argv) {
  if (argc < 2) { fprintf(stderr, "usage: hidtool list|descriptor|get|set|server\n"); return 2; }
  if (strcmp(argv[1], "list") == 0) cmd_list();
  else if (strcmp(argv[1], "watch") == 0) return cmd_watch(argc >= 3 ? atoi(argv[2]) : 3, argc >= 4 ? atoi(argv[3]) : 0);
  else if (strcmp(argv[1], "descriptor") == 0) return cmd_descriptor();
  else if (strcmp(argv[1], "get") == 0 && argc >= 4) return cmd_get(atoi(argv[2]), atoi(argv[3]));
  else if (strcmp(argv[1], "set") == 0 && argc >= 4) {
    unsigned char data[512];
    int n = hex_to_bytes(argv[3], data, sizeof data);
    return cmd_set(atoi(argv[2]), data, n);
  }
  else if (strcmp(argv[1], "sendwatch") == 0 && argc >= 5) {
    unsigned char data[512];
    int n = hex_to_bytes(argv[3], data, sizeof data);
    return cmd_sendwatch(atoi(argv[2]), data, n, atoi(argv[4]));
  }
  else if (strcmp(argv[1], "server") == 0) return cmd_server();
  else { fprintf(stderr, "bad args\n"); return 2; }
  return 0;
}
