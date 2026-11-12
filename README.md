# README.md
# Agent V2

多模态 AI Agent 平台，Router 范式（FC 预留）。

## 架构

协议层 → 应用编排层 → 横向能力层 / 调度层 / 执行层 → Skill 层 → 基础设施层

- Skill 层：一个能力一个 Skill，业务唯一实现（execute / 可选 stream / 可选 on_action）
- 调度层：RouterScheduler（当前），PlannerScheduler（多步），FCScheduler（stub）
- 执行层：Executor 唯一执行入口
- 横向能力：ContextProvider / ReferenceResolver / AttachmentResolver / TaskRunner / ConversationOrchestrator

## 已注册 Skill（23 个）

| 分类 | Skill | Intent | 类型 |
|---|---|---|---|
| general | chat | chat | 同步流式 |
| general | english_chat | english_chat | 同步流式 |
| general | code_generate | code_generation | 同步流式 |
| general | translate_text | translation | 同步流式 |
| general | web_search | web_search | 同步流式 |
| general | utility | utility | 同步 |
| general | tts_synthesize | tts | 同步 + 异步 |
| content | kb_ask | knowledge_query | 同步 |
| content | document_qa | document_qa | 同步 |
| content | document_summarize | document_summarize | 同步（支持批量） |
| content | generate_ppt | ppt_generation | 异步 |
| content | generate_word | word_generation | 异步 |
| content | generate_mindmap | mindmap | 同步 |
| content | generate_prototype | prototype | 同步 |
| image | generate_image | image_generation | 异步 |
| image | edit_image | image_edit | 异步 |
| image | image_understand | image_understanding | 同步 |
| video | text_to_video | text_to_video | 异步 |
| video | image_to_video | image_to_video | 异步 |
| video | generate_storyboard | storyboard | 异步 |
| video | generate_standard_anchor | portrait_animation | 异步 |
| video | generate_custom_avatar | custom_avatar | 异步 |
| video | face_swap | face_swap | 同步 + Flow 多轮 |

## 启动

```powershell
cd E:\agent-v2
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m app.main