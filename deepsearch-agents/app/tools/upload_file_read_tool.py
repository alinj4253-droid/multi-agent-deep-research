"""
上传文件读取工具

供主智能体读取用户在当前会话中上传的临时附件。工具会先通过 ContextVar
拿到本次 session_dir，再把模型传入的文件名解析到真实路径，支持文本、
Word、PDF 和 Excel 等常见格式。
"""

from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from app.api.context import get_session_context
from app.api.monitor import monitor
from app.utils.path_utils import resolve_path

# 文档解析依赖按需导入：缺少某类依赖时，只影响对应文件格式，不影响工具整体注册
try:
    import docx
except ImportError:
    docx = None

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import pandas as pd
except ImportError:
    pd = None


@tool
def read_file_content(
    filename: Annotated[
        str, "要读取的文件名或路径（支持 .md, .docx, .pdf, .xlsx, .xls）"
    ],
    instruction: Annotated[
        str, "对提取内容的具体指令（例如：'提取摘要', '统计数据'）"
    ] = "提取全部内容",
) -> str:
    """
    读取当前会话目录中的指定文件内容

    对于 Excel 文件，会自动提供数据统计信息（head 和 describe）。
    :param filename: 文件名或相对路径，通常由主智能体从上传文件列表中选择
    :param instruction: 模型传入的读取意图，用于监控展示，不改变底层解析逻辑
    :return: 文件文本内容、表格摘要，或中文错误提示
    """
    monitor.report_tool(
        "文件内容读取工具", {"filename": filename, "instruction": instruction}
    )

    # 解析路径时优先约束在当前 session_dir 内，避免模型传入绝对路径导致越界读取
    session_dir = get_session_context()
    file_path = Path(resolve_path(filename, session_dir))

    if not file_path.exists():
        return f"错误：文件 '{filename}' 不存在 (解析路径: {file_path})。"

    # 根据文件后缀选择解析方式；未知后缀会先按 UTF-8 文本兜底读取
    ext = file_path.suffix.lower()

    try:
        if ext in [".md", ".txt"]:
            return file_path.read_text(encoding="utf-8")

        elif ext == ".docx":
            if docx is None:
                return "错误：未安装 'python-docx' 库，无法读取 Word 文件。"
            # python-docx 读取段落文本，适合课程中的普通 Word 附件
            doc = docx.Document(str(file_path))
            full_text = [para.text for para in doc.paragraphs]
            return "\n".join(full_text)

        elif ext == ".pdf":
            if pypdf is None:
                return "错误：未安装 'pypdf' 库，无法读取 PDF 文件。"
            # pypdf 按页提取文本，扫描件或图片型 PDF 可能无法提取有效文字
            reader = pypdf.PdfReader(str(file_path))
            text = "\n".join([page.extract_text() or "" for page in reader.pages])
            return text

        elif ext in [".xlsx", ".xls"]:
            if pd is None:
                return "错误：未安装 'pandas' 库，无法读取 Excel 文件。"

            try:
                df = pd.read_excel(str(file_path))
            except Exception as e:
                return f"读取 Excel 失败: {str(e)}"

            # Excel 不直接返回全量数据，先给模型列名、预览和统计摘要，避免上下文过长
            result = [
                f"文件: {filename}",
                f"行数: {len(df)}, 列数: {len(df.columns)}",
                f"列名: {', '.join(df.columns.astype(str))}",
                "\n[前5行数据预览]:",
                df.head().to_string(index=False),
                "\n[统计描述]:",
                df.describe().to_string(),
            ]
            return "\n".join(result)

        else:
            try:
                return file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"错误：不支持的文件格式 '{ext}'，且无法作为文本读取。"

    except Exception as e:
        return f"读取文件出错: {str(e)}"


if __name__ == "__main__":
    # 本地调试入口：直接运行本文件可验证 Markdown、CSV 等上传文件读取效果。
    # 测试文件在系统临时目录里现场生成，不依赖仓库内任何固定测试数据，
    # 也不会在项目目录留下残留。
    import tempfile

    _debug_dir = tempfile.mkdtemp(prefix="upload_read_debug_")
    _sub = Path(_debug_dir) / "sub_dir"
    _sub.mkdir(parents=True, exist_ok=True)

    # 现场造一个 Markdown 与一个 CSV，覆盖「文本直读」与「结构化预览」两条路径
    (_sub / "测试文件.md").write_text(
        "# 测试文档\n\n这是上传文件读取工具的本地测试内容。", encoding="utf-8"
    )
    (_sub / "测试数据.csv").write_text(
        "指标,数值\n支付转化,0.32\n用户复购,0.18\n", encoding="utf-8"
    )

    # 重新绑定本模块命名空间的 get_session_context，read_file_content 内部会取到它
    def get_session_context():
        return _debug_dir

    print(f"调试目录: {_debug_dir}")

    print("===== 读取 MD 文件结果 =====")
    print(read_file_content.invoke({"filename": "sub_dir/测试文件.md"}))

    print("\n===== 读取 CSV 文件结果（未知后缀走文本兜底）=====")
    print(read_file_content.invoke({"filename": "sub_dir/测试数据.csv"}))

    print("\n===== 读取不存在文件（应返回中文错误提示）=====")
    print(read_file_content.invoke({"filename": "sub_dir/不存在.md"}))
