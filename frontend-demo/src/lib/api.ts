import { API_BASE_URL } from "./config"

export interface TaskResponse {
  status: string
  thread_id: string
}

export interface CancelTaskResponse {
  status: string
  thread_id?: string
  message?: string
}

export interface UploadResponse {
  status: string
  files: string[]
}

export interface OutputFile {
  name: string
  type: string
  path: string
  size: number
  mtime: number
}

export interface FileListResponse {
  files?: OutputFile[]
  error?: string
}

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)
  const contentType = response.headers.get("content-type") || ""
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text()

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`
    throw new Error(message)
  }

  return payload as T
}

export async function startTask(query: string, threadId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(apiUrl("/api/task"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      thread_id: threadId
    })
  })
}

export async function cancelTask(threadId: string): Promise<CancelTaskResponse> {
  return requestJson<CancelTaskResponse>(
    apiUrl(`/api/task/${encodeURIComponent(threadId)}/cancel`),
    {
      method: "POST"
    }
  )
}

export async function uploadSessionFiles(
  files: File[],
  threadId: string
): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append("thread_id", threadId)
  files.forEach((file) => formData.append("files", file))

  return requestJson<UploadResponse>(apiUrl("/api/upload"), {
    method: "POST",
    body: formData
  })
}

export async function listSessionFiles(path: string): Promise<FileListResponse> {
  const url = new URL(apiUrl("/api/files"))
  url.searchParams.set("path", path)
  return requestJson<FileListResponse>(url)
}

export function getDownloadUrl(path: string): string {
  const url = new URL(apiUrl("/api/download"))
  url.searchParams.set("path", path)
  return url.toString()
}
