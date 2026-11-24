// The only names the dashboard answers to. A web page whose own name suddenly points at 127.0.0.1
// (DNS rebinding) keeps its name in the Host header, so it is refused here.
// Same list as LOCAL_HOSTS in campaign/keys.py.
export const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

/** True when the Host header, without its port, is a local name: "[::1]:3000" -> "[::1]". */
export function isLocalHost(host: string | null): boolean {
  const value = (host ?? "").trim().toLowerCase();
  const name = value.endsWith("]") ? value : value.replace(/:\d*$/, "");
  return LOCAL_HOSTS.includes(name);
}

/**
 * True when there is no Origin, or when it is this same dashboard: http:// or https:// plus the Host header.
 * Another web page (or another port, or "null" from a sandboxed frame) is refused.
 */
export function isSameOrigin(origin: string | null, host: string | null): boolean {
  if (origin === null) return true;
  const value = origin.trim().toLowerCase();
  const self = (host ?? "").trim().toLowerCase();
  return self !== "" && (value === `http://${self}` || value === `https://${self}`);
}
