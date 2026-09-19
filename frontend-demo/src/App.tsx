import { useState, useRef, useEffect } from 'react'
import { useDeepAgentSession } from './hooks/useDeepAgentSession'
import type { MonitorMessage } from './types'

// 事件图标和颜色映射
function getEventStyle(event: MonitorMessage) {
  switch (event.event) {
    case 'assistant_call':
      return { icon: '🔀', label: '子智能体调用' }
    case 'tool_start':
      return { icon: '⚙️', label: '工具执行中' }
    case 'tool_end':
      return { icon: '✅', label: '工具完成' }
    case 'task_result':
      return { icon: '📄', label: '任务完成' }
    case 'error':
      return { icon: '❌', label: '错误' }
    case 'session_created':
      return { icon: '💡', label: '会话创建' }
    default:
      return { icon: '•', label: event.event || '信息' }
  }
}

// 把 Markdown 文本渲染成简单 HTML
function renderMarkdown(text: string) {
  if (!text) return null
  return text.split('\n').map((line, i) => {
    if (line.startsWith('## ')) return <h2 key={i} className="text-base font-semibold text-gray-900 mt-4 mb-2">{line.slice(3)}</h2>
    if (line.startsWith('### ')) return <h3 key={i} className="text-sm font-medium text-gray-800 mt-3 mb-1.5">{line.slice(4)}</h3>
    if (line.startsWith('- ')) return <div key={i} className="flex gap-2 text-sm text-gray-700 py-0.5"><span className="text-gray-400">•</span><span>{line.slice(2)}</span></div>
    if (line.startsWith('1. ') || line.startsWith('2. ') || line.startsWith('3. ')) return <div key={i} className="flex gap-2 text-sm text-gray-700 py-0.5"><span className="text-gray-400">{line.slice(0, line.indexOf(' '))}</span><span>{line.slice(line.indexOf(' ') + 1)}</span></div>
    if (line === '') return <div key={i} className="h-2"></div>
    return <p key={i} className="text-sm text-gray-700 leading-relaxed">{line}</p>
  })
}

export default function App() {
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  // 使用真实后端 hook
  const {
    connectionState,
    events,
    files,
    isRunning,
    lastError,
    result,
    submitTask,
    resetSession,
  } = useDeepAgentSession()

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events, result])

  const handleSend = async () => {
    if (!input.trim() || isRunning) return
    const query = input
    setInput('')
    try {
      await submitTask(query)
    } catch (error) {
      alert(error instanceof Error ? error.message : '提交任务失败')
    }
  }

  const handleNewSession = () => {
    resetSession()
  }

  // 连接状态颜色
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
          <button
            onClick={handleNewSession}
            className="w-full py-2 px-4 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            + 新建研究任务
          </button>
        </div>

        {/* 历史会话（暂无数据，后续接入后端历史接口） */}
        <div className="flex-1 overflow-y-auto px-3">
          <p className="text-xs text-gray-400 px-2 mb-2">历史会话</p>
          <p className="text-xs text-gray-300 px-2 py-4 text-center">暂无历史会话</p>
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
              私有文档助手
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
          {events.length === 0 && !result ? (
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
                  onClick={() => setInput('分析上传的数据集，统计人群计数方法的性能对比')}
                >
                  📊 分析上传的数据集，做性能对比
                </button>
                <button
                  className="w-full text-left px-4 py-3 border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                  onClick={() => setInput('结合知识库资料，整理一份 Transformer 视觉模型综述')}
                >
                  📚 整理 Transformer 视觉模型综述
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-6 max-w-3xl mx-auto">
              {/* 用户问题 */}
              {events.length > 0 && (
                <div className="flex justify-end">
                  <div className="max-w-[80%] bg-blue-600 text-white px-4 py-3 rounded-2xl rounded-br-sm">
                    <p className="text-sm leading-relaxed">{input || '研究任务'}</p>
                  </div>
                </div>
              )}

              {/* 执行事件流 */}
              {events.length > 0 && (
                <div className="bg-gray-50 rounded-xl p-4 border border-gray-100">
                  <p className="text-xs text-gray-400 mb-3">执行过程</p>
                  <div className="space-y-2">
                    {events.map((event, idx) => {
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
                    {isRunning && (
                      <div className="flex items-center gap-2 text-blue-600 text-sm">
                        <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                        正在执行...
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* 错误提示 */}
              {lastError && (
                <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                  ⚠️ {lastError}
                </div>
              )}

              {/* 最终结果 */}
              {result && (
                <div className="bg-white border border-gray-200 rounded-xl p-5">
                  <div className="prose prose-sm max-w-none">
                    {renderMarkdown(result)}
                  </div>
                  {/* 生成的文件列表 */}
                  {files.length > 0 && (
                    <div className="mt-4 pt-4 border-t border-gray-100">
                      <p className="text-xs text-gray-400 mb-2">生成的文件</p>
                      <div className="flex flex-wrap gap-2">
                        {files.map((file, idx) => (
                          <span key={idx} className="px-2 py-1 text-xs bg-gray-100 text-gray-700 rounded">
                            📄 {file.name}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
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
              disabled={!input.trim() || isRunning || connectionState !== 'connected'}
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
