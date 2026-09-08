const BASE = import.meta.env.VITE_API_BASE || "/api";

export const token = () => sessionStorage.getItem("access_token") || "";
export async function api<T = any>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (token()) headers.set("Authorization", `Bearer ${token()}`);
  const response = await fetch(BASE + path, { ...init, headers });
  if (response.status === 401) sessionStorage.removeItem("access_token");
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(
      value.message || value.detail || `请求失败 (${response.status})`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export async function streamChat(
  body: object,
  onEvent: (event: string, data: any) => void,
) {
  const response = await fetch(BASE + "/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token()}`,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body)
    throw new Error(`流式问答失败 (${response.status})`);
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (block: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length) onEvent(event, JSON.parse(data.join("\n")));
  };
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    blocks.filter(Boolean).forEach(dispatch);
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
}
