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
