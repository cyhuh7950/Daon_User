import navigationContract from "@daon-user/contracts/navigation.json" with { type: "json" };

export type NativeClientType = "android" | "ios";

type ContractRoute = {
  route_id: string;
  native_route_key?: string | null;
  clients: string[];
  title_key: string;
  states: string[];
};

export type NativeRoute = {
  routeId: string;
  nativeRouteKey: string;
  titleKey: string;
  states: readonly string[];
};

export type NativeRouteProjection =
  | { ok: true; routes: NativeRoute[] }
  | { ok: false; code: "UNKNOWN_NATIVE_CLIENT"; routes: [] };

export type NavigationError = {
  code: "NATIVE_ROUTE_NOT_ALLOWED" | "NATIVE_DEEP_LINK_NOT_ALLOWED";
  rejectedNativeRouteKey: string;
};

export type NavigationState = {
  clientType: NativeClientType;
  routes: NativeRoute[];
  history: string[];
  currentNativeRouteKey: string;
  lastError: NavigationError | null;
};

function isNativeClientType(value: unknown): value is NativeClientType {
  return value === "android" || value === "ios";
}

export function projectNativeRoutes(clientType: unknown): NativeRouteProjection {
  if (!isNativeClientType(clientType)) return { ok: false, code: "UNKNOWN_NATIVE_CLIENT", routes: [] };
  const routes = (navigationContract.routes as ContractRoute[])
    .filter((route) => route.clients.includes(clientType) && typeof route.native_route_key === "string" && route.native_route_key.length > 0)
    .map((route) => ({ routeId: route.route_id, nativeRouteKey: route.native_route_key as string, titleKey: route.title_key, states: Object.freeze([...route.states]) }));
  return { ok: true, routes };
}

export function createNavigationState(clientType: NativeClientType): NavigationState {
  const projection = projectNativeRoutes(clientType);
  if (!projection.ok || projection.routes.length === 0) throw new Error("NATIVE_ROUTE_PROJECTION_EMPTY");
  const first = projection.routes[0].nativeRouteKey;
  return { clientType, routes: projection.routes, history: [first], currentNativeRouteKey: first, lastError: null };
}

function acceptRoute(state: NavigationState, nativeRouteKey: string, code: NavigationError["code"]): NavigationState {
  if (!state.routes.some((route) => route.nativeRouteKey === nativeRouteKey)) {
    return { ...state, lastError: { code, rejectedNativeRouteKey: nativeRouteKey } };
  }
  if (state.currentNativeRouteKey === nativeRouteKey) return { ...state, lastError: null };
  return { ...state, history: [...state.history, nativeRouteKey], currentNativeRouteKey: nativeRouteKey, lastError: null };
}

export function selectNativeRoute(state: NavigationState, nativeRouteKey: string): NavigationState {
  return acceptRoute(state, nativeRouteKey, "NATIVE_ROUTE_NOT_ALLOWED");
}

export function acceptNativeDeepLink(state: NavigationState, nativeRouteKey: string): NavigationState {
  return acceptRoute(state, nativeRouteKey, "NATIVE_DEEP_LINK_NOT_ALLOWED");
}

export function goBack(state: NavigationState): NavigationState {
  if (state.history.length <= 1) return { ...state, lastError: null };
  const history = state.history.slice(0, -1);
  return { ...state, history, currentNativeRouteKey: history.at(-1) as string, lastError: null };
}
