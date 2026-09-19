import { useState, useRef, useEffect } from 'react'

// 类型定义
interface ExecEvent {
  id: string
  type: 'assistant_call' | 'tool_start' | 'tool_end' | 'task_result' | 'info'
  message: string
  agent?: string
  tool?: string
  time: string
}

interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  content: string
  events: ExecEvent[]
  isRunning: boolean
}

interface HistorySession {
  id: string
  title: string
  time: string
}

// 模拟数据：历史会话
const mockHistory: HistorySession[] = [
  { id: '1', title: '3D Gaussian Splatting 研究综述', time: '10:30' },
  { id: '2', title: '人群计数方法对比分析', time: '昨天' },
  { id: '3', title: 'Transformer 视觉模型进展', time: '昨天' },
  { id: '4', title: '点云处理技术调研', time: '3天前' },
]

// 模拟执行事件序列
function generateMockEvents(): ExecEvent[] {
  const now = new Date()
  const events: ExecEvent[] = []
  let seconds = 0

  const add = (type: ExecEvent['type'], message: string, extra?: Partial<ExecEvent>) => {
    seconds += Math.floor(Math.random() * 2) + 1
    events.push({
      id: `${events.length}`,
      type,
      message,
      time: new Date(now.getTime() + seconds * 1000).toLocaleTimeString('zh-CN', { hour12: false }),
      ...extra,
    })
  }

  add('info', '主智能体开始分析任务，规划执行步骤')
  add('assistant_call', '分派任务：网络检索助手', { agent: '网络检索助手' })
  add('tool_start', '正在调用 Tavily 搜索："3D Gaussian Splatting 动态场景"', { tool: '网络搜索' })
  add('tool_end', '搜索完成，获取 8 条相关结果', { tool: '网络搜索' })
  add('assistant_call', '分派任务：数据分析助手', { agent: '数据分析助手' })
  add('tool_start', '执行 Python 代码：统计论文引用数据', { tool: '代码执行' })
  add('tool_end', '数据分析完成，生成可视化图表', { tool: '代码执行' })
  add('assistant_call', '分派任务：私有文档助手', { agent: '私有文档助手' })
  add('tool_start', '检索本地知识库：3DGS 相关论文', { tool: '文档检索' })
  add('tool_end', '知识库检索完成，命中 12 篇相关文档', { tool: '文档检索' })
  add('task_result', '任务执行完成，已生成研究报告')

  return events
}

// 模拟最终回答
const mockAnswer = `## 3D Gaussian Splatting 动态场景重建研究进展

### 核心结论
3D Gaussian Splatting（3DGS）自 2023 年提出以来，已从静态场景重建快速扩展到动态场景领域。当前研究热点集中在以下三个方向：

### 1. 动态 3DGS 方法
- **Deformable 3DGS**：通过引入 deformation field 处理非刚性变形，在人体动画和面部重建上取得 SOTA
- **4D Gaussian Splatting**：将高斯点扩展到时间维度，实现动态场景的实时渲染
- **Raw 3DGS**：直接从原始视频输入优化，无需精确相机标定

### 2. 性能提升
- 训练速度较 NeRF 提升 10-100 倍
- 渲染速度达到实时（>100 FPS）
- 显存占用降低 40%

### 3. 未来方向
- 大规模场景的可扩展性
- 与物理仿真的结合
- 边缘设备上的轻量化部署

详细数据和参考文献已整理到报告文件中。`

// 事件图标组件
function EventIcon({ type }: { type: ExecEvent['type'] }) {
  const icons: Record<ExecEvent['type'], string> = {
    assistant_call: '🔀',
    tool_start: '⚙️',
    tool_end: '✅',
    task_result: '📄',
    info: '💡',
  }
  return <span className="text-sm">{icons[type]}</span>
}

export default function App() {
  const [input, setInput] = useState('')
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [activeSession, setActiveSession] = useState('1')
  const scrollRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [turns])

  const handleSend = async () => {
    if (!input.trim() || isRunning) return

    const userTurn: ChatTurn = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      events: [],
      isRunning: false,
    }

    const assistantTurn: ChatTurn = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: '',
      events: [],
      isRunning: true,
    }

    setTurns(prev => [...prev, userTurn, assistantTurn])
    setInput('')
    setIsRunning(true)

    // 模拟逐步执行事件
    const events = generateMockEvents()
    for (let i = 0; i < events.length; i++) {
      await new Promise(r => setTimeout(r, 800 + Math.random() * 600))
      setTurns(prev => prev.map((t, idx) => {
        if (idx === prev.length - 1) {
          return { ...t, events: [...t.events, events[i]] }
        }
        return t
      }))
    }

    // 最后更新最终回答
    await new Promise(r => setTimeout(r, 500))
    setTurns(prev => prev.map((t, idx) => {
      if (idx === prev.length - 1) {
        return { ...t, content: mockAnswer, isRunning: false }
      }
      return t
    }))
    setIsRunning(false)
  }

  return (
    <div className="flex h-screen bg-white">
      {/* 左侧边栏 */}
      <aside className="w-64 border-r border-gray-100 flex flex-col bg-white">
        {/* 品牌区 */}
        <div className="p-5 border-b border-gray-100">
          <h1 className="text-lg font-semibold text-gray-900">学术研究助手</h1>
          <p className="text-xs text-gray-500 mt-1">多智能体深度研究系统</p>
        </div>

        {/* 新建按钮 */}
        <div className="p-4">
          <button className="w-full py-2 px-4 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
            + 新建研究任务
          </button>
        </div>

        {/* 历史会话 */}
        <div className="flex-1 overflow-y-auto px-3">
          <p className="text-xs text-gray-400 px-2 mb-2">历史会话</p>
          {mockHistory.map(session => (
            <button
              key={session.id}
              onClick={() => setActiveSession(session.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg mb-1 text-sm transition-colors ${
                activeSession === session.id
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              <div className="truncate">{session.title}</div>
              <div className="text-xs text-gray-400 mt-0.5">{session.time}</div>
            </button>
          ))}
        </div>

        {/* 底部：智能体状态 */}
        <div className="p-4 border-t border-gray-100">
          <p className="text-xs text-gray-400 mb-2">智能体</p>
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
              私有文档助手
            </div>
          </div>
        </div>
      </aside>

      {/* 右侧主区域 */}
      <main className="flex-1 flex flex-col bg-white">
        {/* 顶部状态栏 */}
        <header className="h-14 border-b border-gray-100 flex items-center justify-between px-6">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-medium text-gray-900">深度研搜工作台</h2>
          </div>
          <div className={`flex items-center gap-2 text-sm ${isRunning ? 'text-blue-600' : 'text-gray-500'}`}>
            <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-blue-500 animate-pulse' : 'bg-gray-300'}`}></span>
            {isRunning ? '执行中' : '待命'}
          </div>
        </header>

        {/* 对话流 */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
          {turns.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center">
              <div className="w-16 h-16 rounded-full bg-blue-50 flex items-center justify-center mb-4">
                <span className="text-3xl">🔬</span>
              </div>
              <h3 className="text-lg font-medium text-gray-900 mb-2">开始你的深度研究</h3>
              <p className="text-sm text-gray-500 max-w-md">
                输入你的研究问题，系统会自动调度多个智能体协作完成检索、分析和报告生成
              </p>
              <div className="mt-8 space-y-2 w-full max-w-md">
                <button className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => setInput('搜索 2026 年 3D Gaussian Splatting 在动态场景重建中的最新进展，并生成研究报告')}>
                  🔍 搜索 2026 年 3D Gaussian Splatting 动态场景进展
                </button>
                <button className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => setInput('分析上传的数据集，统计人群计数方法的性能对比')}>
                  📊 分析上传的数据集，做性能对比
                </button>
                <button className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => setInput('结合知识库资料，整理一份 Transformer 视觉模型综述')}>
                  📚 整理 Transformer 视觉模型综述
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-6 max-w-3xl mx-auto">
              {turns.map(turn => (
                <div key={turn.id}>
                  {/* 用户消息 */}
                  {turn.role === 'user' && (
                    <div className="flex justify-end">
                      <div className="max-w-[80%] bg-blue-600 text-white px-4 py-3 rounded-2xl rounded-br-sm">
                        <p className="text-sm leading-relaxed">{turn.content}</p>
                      </div>
                    </div>
                  )}

                  {/* Agent 回复 */}
                  {turn.role === 'assistant' && (
                    <div className="space-y-3">
                      {/* 执行事件流 */}
                      {turn.events.length > 0 && (
                        <div className="bg-gray-50 rounded-xl p-4 border border-gray-100">
                          <p className="text-xs text-gray-400 mb-3">执行过程</p>
                          <div className="space-y-2">
                            {turn.events.map((event, idx) => (
                              <div key={event.id} className="flex items-start gap-3 text-sm">
                                <EventIcon type={event.type} />
                                <div className="flex-1">
                                  <span className="text-gray-700">{event.message}</span>
                                  <span className="text-xs text-gray-400 ml-2">{event.time}</span>
                                </div>
                              </div>
                            ))}
                            {turn.isRunning && (
                              <div className="flex items-center gap-2 text-blue-600 text-sm">
                                <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                                正在执行...
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* 最终回答 */}
                      {turn.content && (
                        <div className="bg-white border border-gray-200 rounded-xl p-5">
                          <div className="prose prose-sm max-w-none">
                            {turn.content.split('\n').map((line, i) => {
                              if (line.startsWith('## ')) return <h2 key={i} className="text-base font-semibold text-gray-900 mt-4 mb-2">{line.slice(3)}</h2>
                              if (line.startsWith('### ')) return <h3 key={i} className="text-sm font-medium text-gray-800 mt-3 mb-1.5">{line.slice(4)}</h3>
                              if (line.startsWith('- ')) return <div key={i} className="flex gap-2 text-sm text-gray-700 py-0.5"><span className="text-gray-400">•</span><span>{line.slice(2)}</span></div>
                              if (line === '') return <div key={i} className="h-2"></div>
                              return <p key={i} className="text-sm text-gray-700 leading-relaxed">{line}</p>
                            })}
                          </div>
                          {/* 下载按钮 */}
                          <div className="mt-4 pt-4 border-t border-gray-100 flex gap-2">
                            <button className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition-colors">
                              📄 下载 Markdown
                            </button>
                            <button className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition-colors">
                              📑 下载 PDF
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 底部输入框 */}
        <div className="border-t border-gray-100 px-6 py-4">
          <div className="max-w-3xl mx-auto flex gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              placeholder="输入你的研究任务..."
              disabled={isRunning}
              className="flex-1 px-4 py-3 border border-gray-200 rounded-xl text-sm focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-all disabled:bg-gray-50"
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || isRunning}
              className="px-6 py-3 bg-blue-600 text-white rounded-xl text-sm font-medium hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
            >
              {isRunning ? '执行中' : '发送'}
            </button>
          </div>
          <p className="text-xs text-gray-400 text-center mt-2">
            多智能体协作 · 自动检索 · 智能分析 · 报告生成
          </p>
        </div>
      </main>
    </div>
  )
}
