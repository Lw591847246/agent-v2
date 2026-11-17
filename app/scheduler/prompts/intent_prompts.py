# app/scheduler/prompts/intent_prompts.py
"""L2 意图 Prompt：按 domain 细分，要求输出 params。"""

# ============================================================
# 通用规则（每个 domain prompt 尾部会复用）
# ============================================================
_PARAMS_RULE = """
## params 输出规则
在输出 JSON 的 "params" 字段里，根据意图对应的参数 schema 提取参数：
- 明确能从用户输入提取的字段才放进去，不要编造
- 字符串参数保持原文，不要翻译/改写
- 无法确定的字段不要输出
"""

GENERAL_PROMPT = """你在 general 领域内判断具体意图。可选意图：

- chat: 闲聊、通用问答、写故事/文章/诗歌
- english_chat: 用英文聊天、练英语口语
- code_generation: 写代码、改代码、代码审查、重构
- translation: 翻译（用户明确说"翻译成X语"）
- utility: 本地小工具（不需要联网），包括：
  * 日期时间查询：今天几号、现在几点、今天星期几
  * 数学计算：123*456、3的平方
  * 百分比：100的20%是多少
  * 单位换算：100公斤等于多少斤
  * 温度换算：50华氏度等于多少摄氏度
  * 日期差：今天到明年5月1号还有多少天
  * 随机选择：在苹果和香蕉里选一个
- tts: 朗读、配音、生成语音
- web_search: 需要联网获取外部实时信息，包括天气、新闻、股价/汇率、最新动态

## 关键区分
1. "今天几号"、"现在几点"、"星期几" → utility（本地时间）
2. "今天天气"、"明天南京天气" → web_search
3. "123×456"、"100的20%" → utility（本地计算）
4. "最近新闻"、"苹果股价" → web_search
5. "翻译成英文/日文" → translation；否则 chat，不要因为文本含外文就判翻译
6. "朗读/配音/生成语音" → tts
7. 闲聊、问答、写代码 → chat / code_generation

## needs_agent 判断
单步骤永远 false；只有明确"串联两个以上独立工具"才是 true。

## params 输出
按 intent 对应参数：
- translation: {"text": "...", "target_lang": "英文|日文|..."}
- tts: {"text": "...", "voices": ""}
- code_generation: {"requirement": "...", "language": ""}

## 输出格式（严格 JSON）
{"intent": "chat|english_chat|code_generation|translation|utility|tts|web_search", "confidence": 0.0-1.0, "needs_agent": false, "reasoning": "一句话", "params": {}}
"""


CONTENT_PROMPT = """你在 content 领域内判断具体意图。可选意图：

- document_qa: 针对上传的文档进行提问、解释章节、查找内容
- document_summarize: 概括、提取、整理上传文档的内容（不导出文件）
- ppt_generation: 生成 PPT / 演示文稿 / 幻灯片 / slides
- word_generation: 生成 Word 文档 / docx / 导出成文档文件
- knowledge_query: 查询知识库
- mindmap: 生成思维导图
- prototype: 生成产品原型 / 网页原型 / HTML 设计稿

## 判断规则
1. 明确"生成PPT/做PPT/演示文稿/slides" → ppt_generation
2. 明确"生成Word/做Word/docx/导出文档/保存成文档" → word_generation
3. 有文档附件 + "提问/总结/概括/提取/分析/第三章" → document_qa
4. 有文档附件 + "概括/提取要点/整理成报告" → document_summarize
5. "查知识库/知识库里有什么/关于XX的资料" → knowledge_query
6. "思维导图/脑图/知识树" → mindmap
7. "产品原型/网页原型/HTML设计/页面设计" → prototype
8. 以上都不匹配 → 输出 OUT_OF_DOMAIN

## document_qa vs document_summarize
- "这份文档里XX是什么/第三章讲了什么/解释XX" → document_qa
- "总结/概括/提取要点/整理成报告" → document_summarize
- "总结后导出Word/PPT" → document_summarize + needs_agent=true

## needs_agent 判断
- "提取文档数据再生成PPT" → true
- 单步骤 → false

## params 输出
⚠️ **不要输出 file_id 字段**，系统会自动从用户附件注入。
- document_qa: {"question": "用户的问题"}
- document_summarize: {"user_query": "概括要求"}
- ppt_generation: {"content": "生成 PPT 的内容"}
- word_generation: {"content": "生成 Word 的内容"}
- knowledge_query: {"question": "查询的问题"}
- mindmap: {"topic": "思维导图主题"}
- prototype: {"requirement": "产品需求"}

## 输出格式（严格 JSON）
{"intent": "document_qa|document_summarize|ppt_generation|word_generation|knowledge_query|mindmap|prototype|OUT_OF_DOMAIN", "confidence": 0.0-1.0, "needs_agent": false, "reasoning": "一句话", "params": {}}
"""


IMAGE_PROMPT = """你在 image 领域内判断具体意图。可选意图：

- image_generation: 无图片附件，纯文本生成图片
- image_edit: 有图片附件，修改/换风格/去掉XX/添加XX/变成XX
- image_understanding: 有图片附件，识别/描述/OCR/解题/这是什么

## 判断规则
1. 有图 + "去掉/加上/变成/改成/换风格/美化/去水印/更暗/更亮/编辑/修图" → image_edit
2. 有图 + "这是什么/描述/识别/提取文字/OCR/解题/翻译图片" → image_understanding
3. 无图 + "画一张/画一个/生成图片/画只/画个" → image_generation
4. **有图 + "动/动起来/说话/对口型/配音/让它说" → 输出 OUT_OF_DOMAIN** ⭐ 交给 video 领域
5. 以上都不匹配 → 输出 OUT_OF_DOMAIN

## 输出格式（严格 JSON）
{"intent": "image_generation|image_edit|image_understanding|OUT_OF_DOMAIN", "confidence": 0.0-1.0, "needs_agent": false, "reasoning": "一句话", "params": {}}
"""


VIDEO_PROMPT = """你在 video 领域内判断具体意图。可选意图：

- storyboard: 漫剧/短剧/有声绘本/广播剧/分镜视频
- portrait_animation: 数字人口播、孔子画像讲课、模板授课
- face_swap: 视频换脸
- text_to_video: 纯文本生成视频（无附件）
- image_to_video: 图片动起来（无明确台词/对口型）
- custom_avatar: 定制数字人（有图片 + 具体动作/台词）

## 判断规则
1. "漫剧/短剧/有声绘本/广播剧/分镜视频" → storyboard
2. "孔子/画像/讲课/模板/数字人口播" → portrait_animation
3. 有视频附件 + "换脸/替换人/换成XX" → face_swap
4. 有图 + "让它动起来/加动作" + 无台词 → image_to_video
5. 有图 + "让它说话/对口型/配音/有台词" → custom_avatar
6. 无附件 + "生成一段视频/做视频" → text_to_video
7. 以上都不匹配 → 输出 OUT_OF_DOMAIN

## needs_agent 判断
本领域所有复合 Skill 内部已含多步流程，永远 false。

## params 输出
- storyboard: {"story": "...", "style": "realistic_cinema"}
- portrait_animation: {"speech_text": "..."}
- face_swap: {"prompt": "..."}
- text_to_video: {"prompt": "..."}
- image_to_video: {"image_file_id": "", "prompt": "..."}
- custom_avatar: {"image_file_id": "", "raw_text": "..."}

## 输出格式（严格 JSON）
{"intent": "storyboard|portrait_animation|face_swap|text_to_video|image_to_video|custom_avatar|OUT_OF_DOMAIN", "confidence": 0.0-1.0, "needs_agent": false, "reasoning": "一句话", "params": {}}
"""


L2_PROMPTS = {
    "general": GENERAL_PROMPT,
    "content": CONTENT_PROMPT,
    "image": IMAGE_PROMPT,
    "video": VIDEO_PROMPT,
}

L2_USER_TEMPLATE = """用户输入：{text}
是否有附件：{has_attachment}
附件类型：{attachment_types}"""