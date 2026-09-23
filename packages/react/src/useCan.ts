import { usePage } from "./index";

/** Boolean ability map shared through the backend's auth page props (spec §4.16). */
export type CanMap = Record<string, boolean>;

/**
 * The current page's shared ability map — what the backend's
 * AUTH_SHARED_ABILITIES precomputed for this user. Pages without shared
 * auth props (or without a can map) see an empty object.
 */
export function useCan(): CanMap {
  const { props } = usePage();
  const auth = (props as { auth?: { can?: CanMap } }).auth;
  return auth?.can ?? {};
}
