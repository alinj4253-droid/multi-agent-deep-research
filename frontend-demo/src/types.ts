export type ConnectionState = "connecting" | "connected" | "reconnecting" | "closed"

export interface MonitorMessage {
  type: string
  event: string
  message: string
  data: Record<string, unknown>
  timestamp: string
}

export interface SocketMessage {
  type: string
  message?: string
}

export interface OutputFile {
  name: string
  type: string
  path: string
  size: number
  mtime: number
}

export interface UploadedItem {
  name: string
  raw: File
}
