// Probe: what does IOKit actually say when opening the Hawk Pro interfaces?
// Build: clang -framework IOKit -framework CoreFoundation probe_iokit.c -o probe_iokit
#include <stdio.h>
#include <IOKit/hid/IOHIDManager.h>
#include <IOKit/hid/IOHIDKeys.h>

static const char *ret_name(IOReturn r) {
  switch (r) {
    case kIOReturnSuccess:        return "Success";
    case kIOReturnNotPermitted:   return "NotPermitted";
    case kIOReturnExclusiveAccess:return "ExclusiveAccess";
    case kIOReturnNotPrivileged:  return "NotPrivileged";
    case kIOReturnNotOpen:        return "NotOpen";
    case kIOReturnError:          return "Error";
    case kIOReturnUnsupported:    return "Unsupported";
    default:                      return "?(see value)";
  }
}

static int get_int(IOHIDDeviceRef d, CFStringRef key) {
  CFTypeRef v = IOHIDDeviceGetProperty(d, key);
  int out = -1;
  if (v && CFGetTypeID(v) == CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v, kCFNumberIntType, &out);
  return out;
}

int main(void) {
  IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
  int vid = 0x04F3;
  CFNumberRef vidRef = CFNumberCreate(NULL, kCFNumberIntType, &vid);
  const void *keys[] = { CFSTR(kIOHIDVendorIDKey) };
  const void *vals[] = { vidRef };
  CFDictionaryRef match = CFDictionaryCreate(NULL, keys, vals, 1,
      &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
  IOHIDManagerSetDeviceMatching(mgr, match);
  IOReturn mr = IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);
  printf("IOHIDManagerOpen: 0x%08X (%s)\n\n", mr, ret_name(mr));

  CFSetRef set = IOHIDManagerCopyDevices(mgr);
  if (!set) { printf("no devices\n"); return 1; }
  CFIndex n = CFSetGetCount(set);
  const void **devs = malloc(sizeof(void*) * n);
  CFSetGetValues(set, devs);
  printf("%ld HID devices for VID 0x%04X\n\n", (long)n, vid);

  for (CFIndex i = 0; i < n; i++) {
    IOHIDDeviceRef d = (IOHIDDeviceRef)devs[i];
    int pup = get_int(d, CFSTR(kIOHIDPrimaryUsagePageKey));
    int pu  = get_int(d, CFSTR(kIOHIDPrimaryUsageKey));
    int maxIn = get_int(d, CFSTR(kIOHIDMaxInputReportSizeKey));
    int maxOut = get_int(d, CFSTR(kIOHIDMaxOutputReportSizeKey));
    int maxFeat = get_int(d, CFSTR(kIOHIDMaxFeatureReportSizeKey));
    printf("node %ld: primary=0x%04X:0x%04X  maxIn=%d maxOut=%d maxFeat=%d bits\n",
           (long)i, pup, pu, maxIn, maxOut, maxFeat);

    IOReturn r1 = IOHIDDeviceOpen(d, kIOHIDOptionsTypeNone);
    printf("   open(None)   -> 0x%08X (%s)\n", r1, ret_name(r1));
    if (r1 == kIOReturnSuccess) IOHIDDeviceClose(d, kIOHIDOptionsTypeNone);

    IOReturn r2 = IOHIDDeviceOpen(d, kIOHIDOptionsTypeSeizeDevice);
    printf("   open(Seize)  -> 0x%08X (%s)\n\n", r2, ret_name(r2));
    if (r2 == kIOReturnSuccess) IOHIDDeviceClose(d, kIOHIDOptionsTypeSeizeDevice);
  }
  return 0;
}
