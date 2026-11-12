# app/utils/generate_audio.py
import edge_tts
import asyncio
import os

class TTSService:
    """
    封装 edge-tts 的文本转语音服务
    """
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        """
        :param voice: 默认使用晓晓的语音，可替换为其他语音
        """
        self.voice = voice

    async def synthesize(self, text: str, output_path: str = None) -> str:
        """
        将文本合成为音频文件
        :param text: 要转换的文本
        :param output_path: 输出文件路径，默认为临时文件
        :return: 音频文件的绝对路径
        """
        if output_path is None:
            output_path = os.path.join(os.getcwd(), "temp_tts_output.mp3")

        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_path)

        # 确保文件生成
        if not os.path.exists(output_path):
            raise FileNotFoundError(f"音频文件生成失败: {output_path}")

        return output_path

# 使用示例
if __name__ == "__main__":
    tts = TTSService()
    # 生成音频
    audio_path = asyncio.run(tts.synthesize("你好，欢迎使用AI助手。"))
    print(f"音频已生成: {audio_path}")