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

/** 待上传/已上传的附件条目；raw 为原始 File 对象，供 FormData 提交 */
export interface UploadedItem {
  name: string
  raw: File
}

/**
 * 一轮对话：一次用户提问及其执行过程、产物与最终答案。
 * 多轮对话按数组累积，提交新问题不会覆盖已有轮次。
 */
export interface ChatTurn {
  id: string
  /** 用户本轮提交的原始问题 */
  query: string
  /** 本轮执行过程中推送的事件流 */
  events: MonitorMessage[]
  /** 本轮生成的可下载产物 */
  files: OutputFile[]
  /** 本轮是否仍在执行 */
  isRunning: boolean
  /** 本轮最终答案（Markdown） */
  result: string
  /** 本轮错误信息（若有） */
  error: string
  createdAt: string
}

/** 侧边栏历史会话条目，对应后端 GET /api/threads */
export interface ThreadSummary {
  thread_id: string
  title: string
  preview: string
  has_title: boolean
  steps: number
  /** 该会话工作目录；为空表示目录已不存在（历史产物被清理），无法恢复文件列表 */
  session_path: string
  updated_at: string | null
}

export interface ThreadListResponse {
  threads: ThreadSummary[]
  total: number
  returned: number
}

/** 历史会话中已还原的一轮问答，对应后端 GET /api/threads/{id} */
export interface RestoredTurn {
  query: string
  answer: string
}

export interface ThreadDetailResponse {
  thread_id: string
  found: boolean
  turns: RestoredTurn[]
  turn_count: number
}
