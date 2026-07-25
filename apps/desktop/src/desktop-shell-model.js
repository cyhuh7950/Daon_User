const REQUIRED_NATIVE_KEYS = new Set([
  "Home",
  "WorkspaceDetail",
  "AccountSettings",
  "OrganizationSettings",
  "Operations",
  "Notifications"
]);

export function createWindowsNavigation(routes) {
  return routes
    .filter((route) => route.clients.includes("windows") && REQUIRED_NATIVE_KEYS.has(route.native_route_key))
    .map((route) => ({ ...route, key: route.native_route_key }));
}

export function selectNativeRoute(currentKey, requestedKey, routes) {
  return routes.some((route) => route.key === requestedKey) ? requestedKey : currentKey;
}
