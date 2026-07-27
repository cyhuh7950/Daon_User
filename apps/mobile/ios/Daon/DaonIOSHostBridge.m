#import <React/RCTBridgeModule.h>

@interface RCT_EXTERN_MODULE(DaonIOSHost, NSObject)

RCT_EXTERN_METHOD(saveNavigationRoute:(NSString *)route resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(restoreNavigationRoute:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(getLifecycleState:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(consumePendingDeepLink:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(checkPermission:(NSString *)kind resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(requestPermission:(NSString *)kind resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(openApplicationSettings:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)

@end
