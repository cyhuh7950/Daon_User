const DEFAULT_NATIVE_KEYS = Object.freeze([
  "WorkspaceDetail",
  "Notifications",
  "AccountSettings",
]);

export function createWindowsNavigation(routes, permissionProjection = {}) {
  const allowedKeys = [
    ...DEFAULT_NATIVE_KEYS,
    ...(permissionProjection.organization === true ? ["OrganizationSettings"] : []),
    ...(permissionProjection.operations === true ? ["Operations"] : [])
  ];
  const windowsRoutes = new Map(routes
    .filter((route) => route.clients.includes("windows"))
    .map((route) => [route.native_route_key, route]));
  return allowedKeys
    .filter((key) => windowsRoutes.has(key))
    .map((key) => ({ ...windowsRoutes.get(key), key }));
}

export function selectNativeRoute(currentKey, requestedKey, routes) {
  return routes.some((route) => route.key === requestedKey) ? requestedKey : currentKey;
}
