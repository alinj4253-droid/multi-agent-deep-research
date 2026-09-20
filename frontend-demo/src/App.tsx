import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useDeepAgentSession } from './hooks/useDeepAgentSession'
import { getDownloadUrl, getThreadDetail, listThreads } from './lib/api'
import type { ChatTurn, MonitorMessage, ThreadSummary } from './types'

// 事件图标和文案映射
function getEventStyle(event: MonitorMessage) {
  switch (event.event) {
    case 'assistant_call':
      return { icon: '🔀', label: '子智能体调用' }
    case 'tool_start':
      return { icon: '⚙️', label: '工具执行中' }
    case 'task_result':
      return { icon: '📄', label: '任务完成' }
    case 'task_cancelled':
      return { icon: '⏹️', label: '任务已取消' }
    case 'error':
      return { icon: '❌', label: '错误' }
    case 'session_created':
      return { icon: '💡', label: '会话创建' }
    default:
      return { icon: '•', label: event.event || '信息' }
  }
}

// 文件大小格式化（字节 -> KB/MB）
function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function newTurnId(): string {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// 历史会话时间显示：今天显示时分，更早显示日期
function formatThreadTime(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const hhmm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  if (sameDay) return hhmm
  return `${date.getMonth() + 1}/${date.getDate()} ${hhmm}`
}

// 统一的 Markdown 渲染组件（支持 GFM：表格、删除线、任务列表、代码块等）
function MarkdownText({ text }: { text: string }) {
  return (
    <div className="text-sm text-gray-700 leading-relaxed space-y-2">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ ...props }) => <h1 className="text-lg font-bold text-gray-900 mt-4 mb-2" {...props} />,
          h2: ({ ...props }) => <h2 className="text-base font-semibold text-gray-900 mt-4 mb-2" {...props} />,
          h3: ({ ...props }) => <h3 className="text-sm font-semibold text-gray-800 mt-3 mb-1.5" {...props} />,
          h4: ({ ...props }) => <h4 className="text-sm font-medium text-gray-800 mt-2 mb-1" {...props} />,
          p: ({ ...props }) => <p className="my-1.5" {...props} />,
          ul: ({ ...props }) => <ul className="list-disc pl-5 my-1.5 space-y-1" {...props} />,
          ol: ({ ...props }) => <ol className="list-decimal pl-5 my-1.5 space-y-1" {...props} />,
          li: ({ ...props }) => <li className="marker:text-gray-400" {...props} />,
          strong: ({ ...props }) => <strong className="font-semibold text-gray-900" {...props} />,
          em: ({ ...props }) => <em className="italic" {...props} />,
          a: ({ ...props }) => <a className="text-blue-600 underline break-all" target="_blank" rel="noreferrer" {...props} />,
          blockquote: ({ ...props }) => (
            <blockquote className="border-l-4 border-amber-300 bg-amber-50 px-3 py-1.5 my-2 text-gray-600" {...props} />
          ),
          hr: ({ ...props }) => <hr className="my-3 border-gray-200" {...props} />,
          code: ({ className, children, ...props }) => {
            const isBlock = /language-/.test(className || '')
            if (isBlock) {
              return (
                <code className="block bg-gray-900 text-gray-100 text-xs rounded-lg p-3 overflow-x-auto whitespace-pre-wrap" {...props}>
                  {children}
                </code>
              )
            }
            return (
              <code className="bg-gray-100 text-rose-600 text-xs px-1.5 py-0.5 rounded" {...props}>
                {children}
              </code>
            )
          },
          pre: ({ ...props }) => <pre className="my-2" {...props} />,
          table: ({ ...props }) => (
            <div className="overflow-x-auto my-3">
              <table className="min-w-full border-collapse text-xs" {...props} />
            </div>
          ),
          thead: ({ ...props }) => <thead className="bg-gray-50" {...props} />,
          th: ({ ...props }) => (
            <th className="border border-gray-200 px-3 py-2 text-left font-semibold text-gray-800" {...props} />
          ),
          td: ({ ...props }) => <td className="border border-gray-200 px-3 py-2 align-top" {...props} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

// 单轮对话：用户提问气泡 + 执行过程 + 答案 + 产物文件
function TurnBlock({ turn }: { turn: ChatTurn }) {
  const hasAnswer = Boolean(turn.result.trim())

  return (
    <div className="space-y-4">
      {/* 用户问题 */}
      <div className="flex justify-end">
        <div className="max-w-[85%] bg-blue-600 text-white px-4 py-3 rounded-2xl rounded-br-sm">
          <p className="text-sm leading-relaxed whitespace-pre-wrap">{turn.query}</p>
        </div>
      </div>

      {/* 执行事件流 */}
      {turn.events.length > 0 && (
        <div className="bg-gray-50 rounded-xl p-4 border border-gray-100">
          <p className="text-xs text-gray-400 mb-3">执行过程</p>
          <div className="space-y-2">
            {turn.events.map((event, idx) => {
              const style = getEventStyle(event)
              return (
                <div key={idx} className="flex items-start gap-3 text-sm">
                  <span>{style.icon}</span>
                  <div className="flex-1">
                    <span className="text-xs text-gray-400 mr-2">{style.label}</span>
                    <span className="text-gray-700">{event.message}</span>
                  </div>
                </div>
              )
            })}
            {turn.isRunning && (
              <div className="flex items-center gap-2 text-blue-600 text-sm">
                <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                正在执行...
              </div>
            )}
          </div>
        </div>
      )}

      {/* 本轮错误提示 */}
      {turn.error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
          ⚠️ {turn.error}
        </div>
      )}

      {/* 最终结果 */}
      {hasAnswer ? (
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <MarkdownText text={turn.result} />
        </div>
      ) : (
        !turn.isRunning && (
          <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 text-sm text-gray-500">
            该轮未产生最终回答（可能在完成前被取消，或执行出错）。
          </div>
        )
      )}

      {/* 产物文件列表（点击可下载） */}
      {turn.files.length > 0 && (
        <div className="border border-gray-100 rounded-xl p-4 bg-white">
          <p className="text-xs text-gray-400 mb-2">生成的文件</p>
          <div className="flex flex-col gap-2">
            {turn.files.map((file, idx) => (
              <a
                key={idx}
                href={getDownloadUrl(file.path)}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2 px-3 py-2 text-xs bg-gray-50 hover:bg-blue-50 border border-gray-200 hover:border-blue-300 rounded-lg text-gray-700 hover:text-blue-700 transition-colors"
                title={`下载 ${file.name}`}
              >
                <span>📄</span>
                <span className="flex-1 truncate">{file.name}</span>
                <span className="text-gray-400">{formatFileSize(file.size)}</span>
                <span className="text-blue-600">下载</span>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [input, setInput] = useState('')
  // 多轮对话累积：每次提问追加一个 turn，历史轮次不会被新问题覆盖
  const [turns, setTurns] = useState<ChatTurn[]>([])
  // 历史会话列表与加载态
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [threadsLoading, setThreadsLoading] = useState(false)
  const [threadsError, setThreadsError] = useState('')
  const [restoring, setRestoring] = useState(false)
  // 恢复历史会话产物时可能出现的提示
  const [restoreNote, setRestoreNote] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const {
    adoptSessionPath,
    connectionState,
    events,
    files,
    isCancelling,
    isRunning,
    isUploading,
    lastError,
    resetSession,
    result,
    selectThread,
    submitTask,
    threadId,
    uploadFiles,
    uploadedItems,
    cancelCurrentTask,
  } = useDeepAgentSession()

  // 只有「实时轮次」才接收 hook 推送的事件；历史恢复的轮次必须保持原样。
  // 用 ref 保存当前实时轮次 id，避免异步 setState 造成的错位覆盖。
  const liveTurnIdRef = useRef<string | null>(null)
  // 恢复历史会话时，产物文件由轮询异步返回，需要单独记住挂载到哪一轮
  const filesTargetTurnIdRef = useRef<string | null>(null)

  // 拉取历史会话列表
  const loadThreads = useCallback(async () => {
    setThreadsLoading(true)
    setThreadsError('')
    try {
      const response = await listThreads(40)
      setThreads(response.threads || [])
    } catch (error) {
      setThreadsError(error instanceof Error ? error.message : '历史会话加载失败')
    } finally {
      setThreadsLoading(false)
    }
  }, [])

  // 首次挂载加载历史会话
  useEffect(() => {
    loadThreads()
  }, [loadThreads])

  // 把 hook 的实时状态同步进对应轮次（不影响其他历史轮次）
  useEffect(() => {
    const liveId = liveTurnIdRef.current
    if (liveId) {
      // 实时轮次：事件、产物、运行态、答案、错误全部同步
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === liveId
            ? { ...turn, events, files, isRunning, result, error: lastError }
            : turn
        )
      )
      return
    }

    // 已恢复的历史会话：只同步产物文件（轮询异步返回），问答内容保持还原值
    const filesTargetId = filesTargetTurnIdRef.current
    if (filesTargetId) {
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === filesTargetId ? { ...turn, files } : turn
        )
      )
    }
  }, [events, files, isRunning, result, lastError])

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [turns])

  const handleSend = async () => {
    const query = input.trim()
    if (!query || isRunning) return

    // 先建轮次并标记为实时轮次，后续事件才会写入它
    const turnId = newTurnId()
    liveTurnIdRef.current = turnId
    // 新一轮开始时清空历史会话的文件挂载目标，避免产物错挂到已还原的旧轮次
    filesTargetTurnIdRef.current = null
    setRestoreNote('')
    setTurns((previous) => [
      ...previous,
      {
        id: turnId,
        query,
        events: [],
        files: [],
        isRunning: true,
        result: '',
        error: '',
        createdAt: new Date().toISOString(),
      },
    ])
    setInput('')

    try {
      await submitTask(query)
    } catch (error) {
      const message = error instanceof Error ? error.message : '提交任务失败'
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === turnId ? { ...turn, isRunning: false, error: message } : turn
        )
      )
      alert(message)
    }
  }

  const handleCancel = async () => {
    try {
      await cancelCurrentTask()
    } catch (error) {
      alert(error instanceof Error ? error.message : '取消任务失败')
    }
  }

  const handleNewSession = () => {
    liveTurnIdRef.current = null
    filesTargetTurnIdRef.current = null
    resetSession()
    setTurns([])
    setInput('')
    setRestoreNote('')
    // 新建会话后刷新列表，让刚结束的会话出现在侧边栏
    loadThreads()
  }

  // 点击历史会话：切换 thread_id 并还原该会话的多轮问答与产物
  const handleSelectThread = async (thread: ThreadSummary) => {
    if (isRunning) {
      alert('当前任务仍在执行，请先取消或等待完成后再切换会话')
      return
    }

    setRestoring(true)
    setRestoreNote('')
    try {
      const detail = await getThreadDetail(thread.thread_id)
      // 切换 thread_id：WebSocket 会重连到该会话，后续提问接着这条上下文继续
      liveTurnIdRef.current = null

      const restoredTurns: ChatTurn[] = (detail?.turns || []).map((item, index) => ({
        id: `${thread.thread_id}-restored-${index}`,
        query: item.query,
        events: [],
        isRunning: false,
        result: item.answer,
        error: '',
        createdAt: thread.updated_at || new Date().toISOString(),
        // 产物先留空，由文件轮询异步填充；挂在最后一轮避免每轮重复列同一批文件
        files: [],
      }))

      // 记住产物文件该挂到哪一轮，供文件轮询返回后写入
      filesTargetTurnIdRef.current =
        thread.session_path && restoredTurns.length > 0
          ? restoredTurns[restoredTurns.length - 1].id
          : null

      // 先渲染已还原的问答，再切换 thread_id（selectThread 会清空 hook 状态，
      // 若先切换再 setState，恢复的内容会被随后的实时同步覆盖）
      setTurns(restoredTurns)
      selectThread(thread.thread_id)

      // 接管会话目录，触发文件轮询，恢复该会话已生成的 PDF/PNG 等产物
      if (thread.session_path) {
        adoptSessionPath(thread.session_path)
      }

      if (!detail || restoredTurns.length === 0) {
        setRestoreNote('该会话暂无可还原的问答记录，可以直接继续提问。')
      } else if (!thread.session_path) {
        setRestoreNote('已还原问答内容；该会话的产物目录已被清理，无法恢复文件列表。')
      }
    } catch (error) {
      setRestoreNote(error instanceof Error ? error.message : '恢复历史会话失败')
    } finally {
      setRestoring(false)
    }
  }

  // 附件选择：立即上传到当前会话，供 Agent 后续读取分析
  const handleFilesPicked = async (picked: FileList | null) => {
    if (!picked || picked.length === 0) return
    const items = Array.from(picked).map((file) => ({ name: file.name, raw: file }))
    // 清空 input 值，允许连续选择同名文件
    if (fileInputRef.current) fileInputRef.current.value = ''

    try {
      await uploadFiles(items)
    } catch (error) {
      alert(error instanceof Error ? error.message : '上传失败')
    }
  }

  const connectionColor = {
    connecting: 'bg-yellow-500',
    connected: 'bg-green-500',
    reconnecting: 'bg-orange-500',
    closed: 'bg-red-500',
  }[connectionState]

  const connectionText = {
    connecting: '连接中...',
    connected: '已连接',
    reconnecting: '重连中...',
    closed: '连接断开',
  }[connectionState]

  const isEmptyState = turns.length === 0
  const canSend = Boolean(input.trim()) && !isRunning && connectionState === 'connected'

  return (
    <div className="flex h-screen bg-white">
      {/* 左侧边栏 */}
      <aside className="w-72 border-r border-gray-100 flex flex-col bg-white">
        {/* 品牌区 */}
        <div className="p-5 border-b border-gray-100">
          <h1 className="text-lg font-semibold text-gray-900">学术研究助手</h1>
          <p className="text-xs text-gray-500 mt-1">多智能体深度研究系统</p>
        </div>

        {/* 新建按钮 */}
        <div className="p-4">
          <button
            onClick={handleNewSession}
            className="w-full py-2 px-4 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            + 新建研究任务
          </button>
        </div>

        {/* 历史会话：来自后端 /api/threads，点击可恢复该会话的多轮内容 */}
        <div className="flex-1 overflow-y-auto px-3 min-h-0">
          <div className="flex items-center justify-between px-2 mb-2">
            <p className="text-xs text-gray-400">历史会话</p>
            <button
              onClick={loadThreads}
              className="text-xs text-gray-400 hover:text-blue-600 transition-colors"
              title="刷新历史会话"
            >
              刷新
            </button>
          </div>

          {threadsError && (
            <p className="text-xs text-red-500 px-2 py-2">{threadsError}</p>
          )}

          {threadsLoading ? (
            <p className="text-xs text-gray-300 px-2 py-4 text-center">加载中...</p>
          ) : threads.length === 0 ? (
            <p className="text-xs text-gray-300 px-2 py-4 text-center">暂无历史会话</p>
          ) : (
            <div className="space-y-1">
              {threads.map((thread) => {
                const active = thread.thread_id === threadId
                return (
                  <button
                    key={thread.thread_id}
                    onClick={() => handleSelectThread(thread)}
                    disabled={restoring}
                    className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors disabled:opacity-60 ${
                      active
                        ? 'border-blue-200 bg-blue-50'
                        : 'border-transparent hover:border-gray-200 hover:bg-gray-50'
                    }`}
                    title={thread.preview || thread.title}
                  >
                    <p
                      className={`text-xs leading-snug line-clamp-2 ${
                        active ? 'text-blue-700 font-medium' : 'text-gray-700'
                      }`}
                    >
                      {thread.title}
                    </p>
                    <p className="text-[10px] text-gray-400 mt-1 flex items-center gap-1.5">
                      <span>{formatThreadTime(thread.updated_at)}</span>
                      {thread.steps > 0 && <span>· {thread.steps} 步</span>}
                      {active && <span className="text-blue-500">· 当前</span>}
                    </p>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* 底部：智能体状态 */}
        <div className="p-4 border-t border-gray-100">
          <p className="text-xs text-gray-400 mb-2">智能体团队</p>
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <span className="w-2 h-2 rounded-full bg-green-500"></span>
              网络检索助手
            </div>
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <span className="w-2 h-2 rounded-full bg-green-500"></span>
              数据分析助手
            </div>
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <span className="w-2 h-2 rounded-full bg-green-500"></span>
              学术文献助手
            </div>
          </div>
          {/* 连接状态 */}
          <div className="mt-3 pt-3 border-t border-gray-100">
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <span className={`w-2 h-2 rounded-full ${connectionColor}`}></span>
              {connectionText}
            </div>
          </div>
        </div>
      </aside>

      {/* 右侧主区域 */}
      <main className="flex-1 flex flex-col bg-white min-w-0">
        {/* 顶部状态栏 */}
        <header className="h-14 border-b border-gray-100 flex items-center justify-between px-6">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-sm font-medium text-gray-900">深度研搜工作台</h2>
            <span className="text-xs text-gray-300 font-mono truncate" title={threadId}>
              {threadId.slice(0, 8)}
            </span>
          </div>
          <div className={`flex items-center gap-2 text-sm ${isRunning ? 'text-blue-600' : 'text-gray-500'}`}>
            <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-blue-500 animate-pulse' : 'bg-gray-300'}`}></span>
            {isRunning ? '执行中' : '待命'}
          </div>
        </header>

        {/* 对话流：所有轮次依次累积展示，新问题不会顶掉旧问答 */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
          {isEmptyState ? (
            /* 空状态欢迎页 */
            <div className="h-full flex flex-col items-center justify-center text-center">
              <div className="w-16 h-16 rounded-full bg-blue-50 flex items-center justify-center mb-4">
                <span className="text-3xl">🔬</span>
              </div>
              <h3 className="text-lg font-medium text-gray-900 mb-2">开始你的深度研究</h3>
              <p className="text-sm text-gray-500 max-w-md">
                输入你的研究问题，系统会自动调度多个智能体协作完成检索、分析和报告生成
              </p>
              <div className="mt-8 space-y-2 w-full max-w-md">
                <button
                  className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => setInput('搜索 2026 年 3D Gaussian Splatting 在动态场景重建中的最新进展，并生成研究报告')}
                >
                  🔍 搜索 2026 年 3D Gaussian Splatting 动态场景进展
                </button>
                <button
                  className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => {
                    setInput('分析我上传的数据集，统计关键指标并生成图表')
                    // 直接唤起文件选择，避免出现「引导上传却没有入口」的情况
                    fileInputRef.current?.click()
                  }}
                >
                  📊 上传数据集做统计分析
                </button>
                <button
                  className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => setInput('检索 3D Gaussian Splatting 动态场景重建方向的代表性论文，按年份和引用梳理发展脉络')}
                >
                  📚 检索 3DGS 动态场景方向的代表性论文
                </button>
              </div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto space-y-8">
              {restoreNote && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs text-amber-700">
                  {restoreNote}
                </div>
              )}

              {turns.map((turn, idx) => (
                <div key={turn.id}>
                  {/* 轮次分隔：第二轮起显示序号，便于区分多轮 */}
                  {idx > 0 && (
                    <div className="flex items-center gap-3 my-6">
                      <div className="flex-1 border-t border-gray-100"></div>
                      <span className="text-[10px] text-gray-300">第 {idx + 1} 轮</span>
                      <div className="flex-1 border-t border-gray-100"></div>
                    </div>
                  )}
                  <TurnBlock turn={turn} />
                </div>
              ))}

              {/* 任务已启动但事件尚未到达时的占位 */}
              {isRunning && turns.length > 0 && turns[turns.length - 1].events.length === 0 && (
                <div className="flex items-center gap-2 text-blue-600 text-sm">
                  <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                  任务已提交，等待执行...
                </div>
              )}
            </div>
          )}
        </div>

        {/* 底部输入区：附件 + 多行输入 + 发送/取消 */}
        <div className="border-t border-gray-100 px-6 py-4">
          <div className="max-w-3xl mx-auto">
            {/* 已上传附件（当前会话内 Agent 可读取） */}
            {uploadedItems.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-3">
                {uploadedItems.map((item) => (
                  <span
                    key={`${item.name}-${item.raw.size}`}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-blue-50 border border-blue-100 rounded-full text-xs text-blue-700"
                    title={`已上传：${item.name}`}
                  >
                    📎 {item.name}
                  </span>
                ))}
              </div>
            )}

            <div className="flex items-end gap-2">
              {/* 附件上传入口 */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => handleFilesPicked(e.target.files)}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isRunning || isUploading}
                className="shrink-0 w-11 h-11 flex items-center justify-center border border-gray-200 rounded-xl text-gray-500 hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50 disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed transition-colors"
                title="上传文件（支持 .md .txt .docx .pdf .xlsx .csv）"
              >
                {isUploading ? (
                  <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
                ) : (
                  <span className="text-lg leading-none">📎</span>
                )}
              </button>

              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  // Enter 发送，Shift+Enter 换行
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    handleSend()
                  }
                }}
                placeholder="输入你的研究任务（Enter 发送，Shift+Enter 换行）..."
                disabled={isRunning}
                rows={1}
                className="flex-1 resize-none px-4 py-3 border border-gray-200 rounded-xl text-sm focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-all disabled:bg-gray-50 max-h-40"
              />

              {isRunning ? (
                <button
                  onClick={handleCancel}
                  disabled={isCancelling}
                  className="shrink-0 px-5 py-3 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
                >
                  {isCancelling ? '取消中' : '取消'}
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={!canSend}
                  className="shrink-0 px-6 py-3 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
                >
                  发送
                </button>
              )}
            </div>

            <p className="text-xs text-gray-400 text-center mt-2">
              多智能体协作 · 自动检索 · 智能分析 · 报告生成
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
