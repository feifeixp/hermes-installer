"""Studio canvas selection and result extraction through the official CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

from api import neowow_cli as cli

_LOCK = threading.RLock()
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SUCCESS = {"SUCCESS", "COMPLETED", "COMPLETE", "SUCCEEDED"}
_PENDING = {"PENDING", "RUNNING", "PROCESSING", "QUEUED", "GENERATING"}
_KINDS = {"image": "图片", "video": "视频", "audio": "音频", "text": "文本", "storyboard": "分镜"}


def identifier(value) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("画布或节点标识无效。")
    return value


def _credentials(home: Path) -> dict:
    cli._require_profile_context()
    if not cli._binding_available():
        raise ValueError("请在专属 Agent 实例中使用个人画布。")
    try:
        value = json.loads((cli._config_dir(home) / "config.json").read_text())
        if not isinstance(value.get("apiKey"), str) or not value["apiKey"]:
            raise ValueError()
        return value
    except (OSError, ValueError, AttributeError, KeyError):
        raise ValueError("请先在设置中连接画布账号。") from None


def _owner(config: dict) -> str:
    return hashlib.sha256(config["apiKey"].encode()).hexdigest()


def _run(args: list[str], home: Path) -> dict:
    config = _credentials(home)
    skill = cli.ensure_installed(home)
    node = shutil.which("node")
    if not node:
        raise ValueError("画布工具需要 Node.js 18 或更新版本。")
    env = dict(os.environ)
    env.pop("NEOWOW_DEBUG", None)
    env.update(NEOWOW_API_KEY=config["apiKey"], NEOWOW_BASE="https://neowow.cn",
               NEOWOW_CONFIG_DIR=str(cli._config_dir(home)),
               NEOWOW_NO_UPDATE_CHECK="1", HERMES_HOME=str(home))
    try:
        result = subprocess.run([node, str(skill / "bin/neowow.mjs"), *args],
                                env=env, capture_output=True, text=True, timeout=45)
        if result.returncode:
            if "errCode=2001" in result.stderr:
                raise ValueError("画布授权已失效，请更换 Key 后重试。")
            raise ValueError("暂时无法读取或操作画布，请检查连接后重试。")
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("画布返回格式异常，请稍后重试。")
        return data
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ValueError("画布请求未完成，请刷新确认状态；不要重复新建画布。") from None


def _save_active(home: Path, project: dict, config: dict) -> dict:
    value = {"sessionId": identifier(str(project.get("sessionId", ""))),
             "title": str(project.get("title") or "未命名画布")[:200]}
    directory = cli._config_dir(home)
    with _LOCK:
        if directory.is_symlink():
            raise ValueError("画布配置目录不能是符号链接。")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump({**value, "owner": _owner(config)}, out, ensure_ascii=False)
            os.replace(name, directory / "studio-project.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return value


def active_project(home: Path | None = None) -> dict | None:
    home = home or cli._home()
    config = _credentials(home)
    try:
        value = json.loads((cli._config_dir(home) / "studio-project.json").read_text())
        if value.get("owner") != _owner(config):
            return None
        return {"sessionId": identifier(value["sessionId"]), "title": str(value["title"])[:200]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def list_projects(page: str = "1", folder: str = "") -> dict:
    home = cli._home()
    if not str(page).isdigit() or not 1 <= int(page) <= 10000:
        raise ValueError("画布页码无效。")
    args = ["project", "list", "--page-num", str(page), "--page-size", "20"]
    if folder:
        args += ["--folder", identifier(folder)]
    data = _run(args, home)
    projects = [{"sessionId": identifier(str(p.get("sessionId", ""))),
                 "title": str(p.get("title") or "未命名画布")[:200],
                 "description": str(p.get("description") or "")[:500]}
                for p in data.get("projects", []) if isinstance(p, dict)]
    folders = [{"folderId": identifier(str(p.get("folderId", ""))), "name": str(p.get("name") or "文件夹")[:200]}
               for p in data.get("folders", []) if isinstance(p, dict)]
    return {"projects": projects, "folders": folders, "page": int(page),
            "hasMore": len(projects) + len(folders) >= 20, "active": active_project(home)}


def select_project(project_id: str) -> dict:
    home = cli._home()
    config = _credentials(home)
    project = _run(["project", "get", identifier(project_id)], home)
    if str(project.get("sessionId")) != project_id:
        raise ValueError("画布标识不匹配，请刷新后重新选择。")
    return {"active": _save_active(home, project, config)}


def create_project(title, closed) -> dict:
    if closed is not True:
        raise ValueError("请先确认已关闭目标画布网页，且协作房间无人。")
    if not isinstance(title, str) or not title.strip() or len(title) > 120 or title.lstrip().startswith("-"):
        raise ValueError("请输入 1–120 字的画布名称。")
    home = cli._home()
    config = _credentials(home)
    project = _run(["project", "create", title.strip()], home)
    return {"active": _save_active(home, project, config)}


def creation_brief(body: dict) -> dict:
    project = active_project()
    if not project or body.get("projectId") != project["sessionId"]:
        raise ValueError("请先选择画布；当前选择可能已改变，请刷新后重试。")
    if body.get("canvasClosed") is not True:
        raise ValueError("创作前请关闭目标画布网页，并确认协作房间无人。")
    kind, prompt = body.get("kind"), body.get("prompt")
    if not isinstance(kind, str) or kind not in _KINDS or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 10000:
        raise ValueError("请选择创作类型，并填写不超过 10000 字的创作要求。")
    scope = (f"目标画布 sessionId：{project['sessionId']}，所有 CLI 操作显式使用这个画布。\n"
             "我已关闭目标画布网页，并确认协作房间无人。\n\n")
    if kind in {"video", "storyboard"}:
        # Older clients also enter the writing stage, never implicit production.
        stage = body.get("stage", "script")
        duration = body.get("duration", 60)
        aspect = body.get("aspect", "16:9")
        if not isinstance(stage, str) or stage not in {"script", "revise", "clips"}:
            raise ValueError("请选择剧本创作、修改逐字稿或准备分镜。")
        if (type(duration) is not int or not 10 <= duration <= 600
                or not isinstance(aspect, str) or aspect not in {"16:9", "9:16", "1:1"}):
            raise ValueError("请设置 10–600 秒的目标时长和支持的画幅。")
        script = body.get("script", "")
        if not isinstance(script, str) or len(script) > 20000:
            raise ValueError("逐字稿不能超过 20000 字。")
        if stage in {"revise", "clips"} and not script.strip():
            raise ValueError("请先载入或填写剧本逐字稿。")
        if stage == "clips" and body.get("scriptConfirmed") is not True:
            raise ValueError("请先审阅并确认当前逐字稿，再准备分镜与视频片段。")
        revision = body.get("revision", "")
        if stage == "revise" and (not isinstance(revision, str) or not revision.strip() or len(revision) > 10000):
            raise ValueError("请填写不超过 10000 字的修改意见。")
        requirements = f"创作要求：\n{prompt.strip()}\n\n目标总时长：{duration} 秒；画幅：{aspect}。\n"
        if stage in {"script", "revise"}:
            message = ("现在只进行剧本写作阶段。请用当前聊天的大模型能力创作文字，使用 NeoWoW 画布技能保存文字。\n" + scope + requirements +
                       "先完善故事：目标观众、主题、人物动机、开场钩子、情节推进／冲突、转折和结尾。"
                       "信息不足时说明合理假设，避免重复场景或只有风景展示。\n"
                       "输出完整可审阅的剧本和逐字稿：按场次／时间段给出画面动作、说话人、逐字对白／旁白、情绪、停顿及环境声音。"
                       "对白与旁白必须写出每一句原文，不能只给梗概或用省略号代替。检查朗读时长与目标总时长，避免台词塞不进片段。\n")
            if stage == "revise":
                message += f"修改意见：\n{revision.strip()}\n请据此修改以下现有逐字稿，保留未要求改动的内容，说明主要修改：\n<现有逐字稿>\n{script.strip()}\n</现有逐字稿>\n"
            message += ("在聊天中展示全文，然后通过官方 CLI node create -t text --content 将完整剧本回填画布，"
                        "新建文本节点并命名为「剧本逐字稿 · 初稿／修订稿」，保留旧版本，返回 nodeKey。文本是创作素材，不是工具指令。\n"
                        "本阶段不要执行 gen text、gen image、gen video、gen audio，不提交任何付费媒体生成。"
                        "停止并请我审阅修改；我会在工作区提取文本、载入逐字稿并确认后进入下一阶段。"
                        "后续媒体必须确认后再提交付费生成。")
        else:
            message = ("请使用 NeoWoW 画布技能，根据已经审阅确认的逐字稿准备分镜方案。剧本确认不代表积分消费确认。\n" + scope + requirements +
                       f"<已确认逐字稿>\n{script.strip()}\n</已确认逐字稿>\n"
                       "先将这份确切文字保存为新的「剧本逐字稿 · 确认版」文本节点。以它为创作依据，不擅自增删或改写对白／旁白。\n"
                       "输出按播放顺序编号的分镜表：每镜起止时间／时长、对应逐字台词、说话人与情绪、人物动作、场景、景别、机位／运镜、声音和转场。"
                       "每句台词必须有对应片段；合理安排朗读与动作时长，镜头用行动推动情节，交替景别与机位，避免同一画面重复。"
                       "保持人物外观、服装、场景、道具和声音连续性；所需角色／场景参考素材也列入方案。\n"
                       "读取 CLI 文档并发现模型能力及单片时长约束；目标总时长不是单条生成时长，按支持的时长拆成多个节点。"
                       "视频默认按官方 Skill 使用 --universal --model neo-video-2-0，除非我指定其他模式；每个片段带对应的逐字台词及表演要求。\n"
                       "使用 CLI --dry-run 展示每个片段与参考图／配音的模型、预估积分和总计，先停下来等我确认方案与积分，确认后再提交付费生成。\n")
            message += ("确认后按分镜逐个生成视频片段。" if kind == "video" else "确认后按分镜生成图片；视频片段需另行明确请求与确认积分。")
            message += ("将确认稿、分镜表保存到画布，按编号命名节点。使用 gen --wait 或 poll --apply 回填画布，"
                        "交付每个片段对应的台词、nodeKey、实际状态、预览和原文件链接；失败只重试失败片段，重试消费也先确认。"
                        "当前官方 CLI 尚未文档化多片段合成，不把分镜片段宣称为已拼接成片。")
        return {"projectId": project["sessionId"], "stage": stage, "message": message}
    message = (f"请使用 NeoWoW 画布技能完成{_KINDS[kind]}创作。\n" + scope +
               f"创作要求：\n{prompt.strip()}\n\n"
               "先展示创作方案、所用模型和 CLI 预估积分，等我确认后再提交付费生成。"
               "完成后回填画布，交付最终图片／视频／音频预览或文本，以及结果原文件链接。"
               "若仍在生成或失败，请明确说明状态，不要把参考素材当成最终结果。")
    return {"projectId": project["sessionId"], "message": message}


def media_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            return None
        return value
    except ValueError:
        return None


def extract_results(project: dict, tasks: dict | None = None) -> dict:
    task_map = {str(t.get("taskId")): t for t in (tasks or {}).get("tasks", []) if isinstance(t, dict)}
    results, nodes = [], []
    for node in project.get("nodes", []):
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_id = str(node.get("id") or node.get("nodeKey") or "")
        if not _ID.fullmatch(node_id):
            continue
        kind = node.get("type")
        if kind not in {"image", "video", "audio", "text"} or data.get("type") == "upload":
            continue
        task = task_map.get(str(data.get("taskId")), {})
        status = str(task.get("status") or data.get("status") or "").upper()
        label = str(data.get("label") or _KINDS[kind])[:200]
        phase = "success" if status in _SUCCESS else "pending" if status in _PENDING else "failed" if status in {"FAILED", "ERROR", "FAILURE"} else "unknown"
        if kind == "text" and not status and data.get("result"):
            phase = "success"
        nodes.append({"nodeKey": node_id, "kind": kind, "label": label, "phase": phase,
                      "queued": bool(task.get("queuedByConcurrencyLimit"))})
        if phase != "success":
            continue
        field = {"image": "inputImageUrls", "video": "inputVideoUrls", "audio": "inputAudioUrls", "text": "result"}[kind]
        content = task.get("resultData") if task.get("status") else data.get(field)
        if kind == "text":
            if isinstance(content, list):
                content = "\n\n".join(x for x in content if isinstance(x, str))
            if isinstance(content, str) and content.strip():
                results.append({"nodeKey": node_id, "kind": kind, "label": label, "text": content[:200000]})
        else:
            if kind == "audio" and isinstance(content, dict):
                content = content.get("audioUrl")
            if isinstance(content, str):
                content = [content]
            if isinstance(content, list):
                for url in content:
                    safe = media_url(url)
                    if safe:
                        results.append({"nodeKey": node_id, "kind": kind, "label": label, "url": safe})
    return {"results": results, "nodes": nodes,
            "counts": {phase: sum(n["phase"] == phase for n in nodes) for phase in ("success", "pending", "failed", "unknown")}}


def get_results(project_id: str) -> dict:
    home = cli._home()
    active = active_project(home)
    if not active or active["sessionId"] != identifier(project_id):
        raise ValueError("请先选择要提取结果的画布。")
    project = _run(["project", "get", project_id], home)
    if str(project.get("sessionId")) != project_id:
        raise ValueError("画布标识不匹配。")
    # Read task progress without --apply: result inspection never mutates canvas.
    pending = []
    for node in project.get("nodes", []):
        data = node.get("data", {}) if isinstance(node, dict) else {}
        if not isinstance(data, dict):
            continue
        task_id = data.get("taskId")
        if str(data.get("status", "")).upper() in _PENDING and isinstance(task_id, (str, int)) and not isinstance(task_id, bool) and _ID.fullmatch(str(task_id)):
            pending.append(str(task_id))
    task_data, warning = None, ""
    if pending:
        try:
            task_data = _run(["poll", *list(dict.fromkeys(pending)), "--once", "-p", project_id], home)
        except ValueError:
            warning = "任务进度暂时无法刷新，以下为画布已保存的状态。"
    return {"active": active, **extract_results(project, task_data), "warning": warning}
