// Feature-report probe on the Hawk Pro config interface, via IOKit.
// Build: clang -framework IOKit -framework CoreFoundation hidtest.c -o hidtest
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <IOKit/hid/IOHIDManager.h>
#include <IOKit/hid/IOHIDKeys.h>

static int get_int(IOHIDDeviceRef d, CFStringRef key) {
  CFTypeRef v = IOHIDDeviceGetProperty(d, key);
  int out = -1;
  if (v && CFGetTypeID(v) == CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v, kCFNumberIntType, &out);
  return out;
}

static void dump_cfstring(IOHIDDeviceRef d, CFStringRef key) {
  CFTypeRef v = IOHIDDeviceGetProperty(d, key);
  if (v && CFGetTypeID(v) == CFStringGetTypeID()) {
    char buf[256];
    CFStringGetCString((CFStringRef)v, buf, sizeof buf, kCFStringEncodingUTF8);
    printf("  %s = %s\n", "prop", buf);
  }
}

int main(void) {
  IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
  int vid = 0x04F3, pid = 0x026E;
  CFNumberRef vidRef = CFNumberCreate(NULL, kCFNumberIntType, &vid);
  CFNumberRef pidRef = CFNumberCreate(NULL, kCFNumberIntType, &pid);
  const void *keys[] = { CFSTR(kIOHIDVendorIDKey), CFSTR(kIOHIDProductIDKey) };
  const void *vals[] = { vidRef, pidRef };
  CFDictionaryRef match = CFDictionaryCreate(NULL, keys, vals, 2,
      &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
  IOHIDManagerSetDeviceMatching(mgr, match);
  IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);

  CFSetRef set = IOHIDManagerCopyDevices(mgr);
  CFIndex n = set ? CFSetGetCount(set) : 0;
  const void **devs = n ? malloc(sizeof(void*) * n) : NULL;
  if (n) CFSetGetValues(set, devs);

  IOHIDDeviceRef target = NULL;
  for (CFIndex i = 0; i < n; i++) {
    IOHIDDeviceRef d = (IOHIDDeviceRef)devs[i];
    int maxFeat = get_int(d, CFSTR(kIOHIDMaxFeatureReportSizeKey));
    int pup = get_int(d, CFSTR(kIOHIDPrimaryUsagePageKey));
    int pu = get_int(d, CFSTR(kIOHIDPrimaryUsageKey));
    int maxIn = get_int(d, CFSTR(kIOHIDMaxInputReportSizeKey));
    printf("node: primary=0x%04X:0x%04X maxFeatureBits=%d maxInputBits=%d\n", pup, pu, maxFeat, maxIn);
    dump_cfstring(d, CFSTR(kIOHIDProductKey));
    if (maxFeat > 0) target = d;
  }

  if (!target) { printf("no feature-capable node\n"); return 1; }
  IOReturn r = IOHIDDeviceOpen(target, kIOHIDOptionsTypeNone);
  printf("open target -> 0x%08X\n", r);
  if (r != kIOReturnSuccess) return 1;

  int maxFeatBytes = get_int(target, CFSTR(kIOHIDMaxFeatureReportSizeKey)) / 8;
  if (maxFeatBytes <= 0) maxFeatBytes = 17;
  printf("scanning feature GetReport ids 0..255, len=%d (printing only successes):\n", maxFeatBytes);

  int successes = 0;
  for (int rid = 0; rid <= 255; rid++) {
    unsigned char buf[512];
    memset(buf, 0, sizeof buf);
    CFIndex nbytes = maxFeatBytes;
    IOReturn gr = IOHIDDeviceGetReport(target, kIOHIDReportTypeFeature, rid, buf, &nbytes);
    if (gr == kIOReturnSuccess) {
      successes++;
      printf("  id=%3d (0x%02X) got=%ld: ", rid, rid, (long)nbytes);
      for (CFIndex k = 0; k < nbytes; k++) printf("%02X ", buf[k]);
      printf("\n");
    }
  }
  printf("%d successful feature reads\n", successes);

  IOHIDDeviceClose(target, kIOHIDOptionsTypeNone);
  return 0;
}
