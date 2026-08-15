import logging
import os
import subprocess
import json
import asyncio
import sys
import glob
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
import aiohttp 
import pysrt

# --- LOGLAMA YAPILANDIRMASI ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('log.txt', encoding='utf-8'), logging.StreamHandler()],
    level=logging.INFO
)
LOGGER = logging.getLogger(__name__)

load_dotenv()

# --- YAPILANDIRMA VE SABİTLER ---
try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    STRING_SESSION = os.getenv("STRING_SESSION")
    LOG_CHANNEL = int(os.getenv("LOG_CHANNEL")) if os.getenv("LOG_CHANNEL") else None
    
    class Config:
        STREAMTAPE_API_USERNAME = os.getenv("STREAMTAPE_API_USERNAME")
        STREAMTAPE_API_PASS = os.getenv("STREAMTAPE_API_PASS")
        
    if not Config.STREAMTAPE_API_USERNAME or not Config.STREAMTAPE_API_PASS:
        raise ValueError("Streamtape API bilgileri eksik.")
        
    if not STRING_SESSION:
        raise ValueError("STRING_SESSION .env dosyasında bulunamadı.")
        
    if not LOG_CHANNEL:
        raise ValueError("LOG_CHANNEL .env dosyasında bulunamadı.")

except (TypeError, ValueError) as e:
    LOGGER.error(f"HATA: Yapılandırma hatası: {e}")
    exit(1)

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
UNZIP_SUBDIR = "unzip" 

Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
UNZIP_PATH = Path(DOWNLOAD_DIR) / UNZIP_SUBDIR
UNZIP_PATH.mkdir(exist_ok=True)

# --- STRING SESSION İLE PYROGRAM CLIENT ---
app = Client(
    "archive_userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION
)

task_queue = asyncio.Queue()
is_processing = False

# --- TERMINAL PROGRESS BAR ---
def draw_bar(label, current, total):
    percent = (current * 100 / total) if total else 0
    bar = int(percent // 5) * "█" + (20 - int(percent // 5)) * "-"
    sys.stdout.write(f"\r{label} |{bar}| {percent:.1f}%")
    sys.stdout.flush()

def download_progress(c, t):
    draw_bar("📥 DOWNLOAD", c, t)

def upload_progress(c, t):
    draw_bar("📤 UPLOAD", c, t)

def format_release_name(path, tag):
    p = Path(path)
    return str(p.parent / f"{p.stem}-{tag}.mp4")


# --- VİDEO BİLGİ YARDIMCILARI ---

def get_duration(file):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return int(float(result.stdout.strip()))
    except Exception:
        return 0

def get_video_info(file):
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", file]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        w, h = result.stdout.strip().split('x')
        return int(w), int(h)
    except Exception:
        return 0, 0

def get_thumbnail(file):
    try:
        thumb_path = f"{file}_thumb.jpg"
        duration = get_duration(file)
        ss_time = str(max(1, duration // 2))
        cmd = ["ffmpeg", "-y", "-ss", ss_time, "-i", file, "-vframes", "1", "-q:v", "2", thumb_path]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        LOGGER.error(f"Thumbnail alma hatası: {e}")
    return None


# --- İŞLEME VE ALTYAZI FONKSİYONLARI ---

def add_custom_subtitle(input_srt, output_srt, text, start, end):
    try:
        subs = pysrt.open(input_srt)
        new = pysrt.SubRipItem()
        new.start = pysrt.SubRipTime.from_string(start)
        new.end = pysrt.SubRipTime.from_string(end)
        new.text = (
            '{\\an8}<font color="white">Bu İçerik</font><br>'
            f'<font color="green">{text}</font>'
        )
        subs.insert(0, new)
        subs.save(output_srt, encoding="utf-8")
        return True
    except Exception as e:
        LOGGER.error(f"Altyazı ekleme hatası: {e}")
        return False

async def extract_tr_audio(path):
    try:
        cmd_probe = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index:stream_tags=language",
            "-of", "json", path
        ]
        proc = await asyncio.create_subprocess_exec(*cmd_probe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        data = json.loads(stdout.decode('utf-8'))

        for s in data.get("streams", []):
            lang = s.get("tags", {}).get("language", "").lower()
            if lang.startswith("tr") or lang.startswith("tur"):
                audio_index = s["index"]
                out = format_release_name(path, "TR")
                
                cmd = [
                    "ffmpeg", "-y", "-i", path,
                    "-map", "0:v:0", "-map", f"0:{audio_index}",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    out
                ]
                proc_ff = await asyncio.create_subprocess_exec(*cmd)
                await proc_ff.communicate()
                
                if proc_ff.returncode == 0 and os.path.exists(out):
                    try: os.remove(path)
                    except Exception: pass
                    return out
        return None
    except Exception as e:
        LOGGER.error(f"TR Ses çıkarma hatası: {e}")
        return None

async def extract_subtitle(path):
    try:
        cmd_probe = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path]
        proc = await asyncio.create_subprocess_exec(*cmd_probe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        data = json.loads(stdout.decode('utf-8'))

        for s in data.get("streams", []):
            if s.get("codec_type") == "subtitle":
                lang = s.get("tags", {}).get("language", "").lower()
                if lang in ["tr", "tur", "trk", "turkish"] or not lang:
                    out = str(Path(path).parent / "raw_sub.srt")
                    cmd = ["ffmpeg", "-y", "-i", path, "-map", f"0:{s['index']}", out]
                    proc_ff = await asyncio.create_subprocess_exec(*cmd)
                    await proc_ff.communicate()
                    
                    if proc_ff.returncode == 0 and os.path.exists(out):
                        return out
        return None
    except Exception as e:
        LOGGER.error(f"Altyazı ayıklama hatası: {e}")
        return None

async def hardmux(video, sub):
    try:
        out = format_release_name(video, "TRSub")
        sub_path_escaped = sub.replace("\\", "/").replace(":", "\\:")
        
        cmd = [
            "ffmpeg", "-y", "-i", video,
            "-vf", f"subtitles='{sub_path_escaped}'",
            "-map", "0:v", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "superfast",
            "-c:a", "aac", "-b:a", "192k",
            out
        ]
        proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.communicate()

        if proc.returncode == 0 and os.path.exists(out):
            try:
                os.remove(video)
                os.remove(sub)
            except Exception: pass
            return out
        return None
    except Exception as e:
        LOGGER.error(f"Hardmux hatası: {e}")
        return None

async def process_media(file):
    print(f"\n🎬 Medya İşleniyor: {Path(file).name}")
    
    # 1. TR Ses Kontrolü
    tr_audio = await extract_tr_audio(file)
    if tr_audio:
        print("✅ Türkçe Ses kanalı bulundu ve işlendi.")
        return tr_audio

    print("⚠️ TR Ses bulunamadı. Altyazı taranıyor...")
    
    # 2. TR Altyazı Kontrolü & Hardsub
    sub = await extract_subtitle(file)
    if sub:
        new_sub = sub.replace("raw_sub.srt", "reklamli_sub.srt")
        added = add_custom_subtitle(
            sub,
            new_sub,
            "Telegram: @dublajflix tarafından hazırlanmıştır...",
            "00:00:00,000",
            "00:01:00,000"
        )
        if added and os.path.exists(new_sub):
            print("🎨 Özel Reklam Altyazısı Eklendi. Hardmux Başlatılıyor...")
            hmux_res = await hardmux(file, new_sub)
            if hmux_res:
                print("✅ Hardsub işlemi başarıyla tamamlandı.")
                return hmux_res

    # 3. Varsayılan (Yedek) Encode İşlemi
    print("⚠️ Altyazı da bulunamadı. Varsayılan dönüştürme yapılıyor...")
    out = format_release_name(file, "TR")
    cmd = [
        "ffmpeg", "-y", "-i", file,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "superfast",
        "-c:a", "aac", "-b:a", "192k",
        out
    ]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.communicate()

    if proc.returncode == 0 and os.path.exists(out):
        try: os.remove(file)
        except Exception: pass
        return out

    return None


# --- TELEGRAM UPLOAD FONKSİYONU (< 4GB) ---

async def upload(client, file, original_caption):
    print(f"\n📤 Telegram Log Kanalına Yükleniyor: {Path(file).name}")
    thumb = get_thumbnail(file)
    duration = get_duration(file)
    width, height = get_video_info(file)

    new_filename = os.path.basename(file)

    if original_caption:
        lines = original_caption.split("\n")
        lines[0] = f"__{new_filename}__"
        
        if len(lines) > 1:
            desc = "\n".join(lines[1:]).strip()
            if desc:
                caption = f"{lines[0]}\n||{desc}||"
            else:
                caption = lines[0]
        else:
            caption = lines[0]
    else:
        caption = f"__{new_filename}__"

    try:
        await client.send_video(
            LOG_CHANNEL,
            file,
            caption=caption,
            thumb=thumb,
            duration=duration,
            width=width,
            height=height,
            supports_streaming=True,
            progress=upload_progress
        )
        print() # Terminal alt satıra geçiş
        return True
    except Exception as e:
        print()
        LOGGER.error(f"Telegram yükleme hatası: {e}")
        return False
    finally:
        if thumb and os.path.exists(thumb):
            try: os.remove(thumb)
            except Exception: pass


# --- STREAMTAPE UPLOAD FONKSİYONU (>= 4GB) ---

async def upload_to_streamtape(client: Client, chat_id: int, path_to_file: str) -> bool:
    if not os.path.exists(path_to_file):
        await client.send_message(chat_id, f"❌ Yüklenecek dosya bulunamadı: {Path(path_to_file).name}")
        return False
        
    status_msg = await client.send_message(chat_id, f"📤 `{Path(path_to_file).name}` Streamtape'e yükleniyor...")
    
    try:
        timeout = aiohttp.ClientTimeout(total=1800)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            main_api = f"https://api.streamtape.com/file/ul?login={Config.STREAMTAPE_API_USERNAME}&key={Config.STREAMTAPE_API_PASS}"
            
            hit_api = await session.get(main_api)
            json_data = await hit_api.json()
            
            if json_data.get("status") != 200:
                await status_msg.edit_text(f"❌ API Hatası: {json_data.get('msg')}")
                return False

            temp_api = json_data["result"]["url"]
            filename = Path(path_to_file).name
            total_size = os.path.getsize(path_to_file)
            uploaded_bytes = 0

            async def file_sender():
                nonlocal uploaded_bytes
                with open(path_to_file, 'rb') as f:
                    while chunk := f.read(1024 * 1024):
                        uploaded_bytes += len(chunk)
                        upload_progress(uploaded_bytes, total_size)
                        yield chunk

            data = aiohttp.FormData()
            data.add_field('file1', file_sender(), filename=filename, content_type='video/mp4')
            
            async with session.post(temp_api, data=data) as response:
                print()
                try: data_f = await response.json(content_type=None)
                except Exception: data_f = {}

            status = data_f.get("status")
            download_link = data_f.get("result", {}).get("url")
            
            if status != 200 or not download_link:
                await status_msg.edit_text(f"❌ Yükleme Başarısız: {data_f.get('msg')}")
                return False

            await status_msg.edit_text(
                f"🎉 **Streamtape Yüklemesi Başarılı!**\n\n"
                f"📁 **Dosya:** `{filename}`\n"
                f"🔗 **Link:** {download_link}",
                disable_web_page_preview=True
            )
            return True
            
    except Exception as e:
        print()
        LOGGER.error(f"Streamtape yükleme hatası: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Yükleme Esnasında Hata: {e}")
        return False


# --- KUYRUĞU İŞLEYEN MOTOR ---

async def process_queue():
    global is_processing
    is_processing = True
    
    ARCHIVE_EXTENSIONS = ('.zip', '.rar', '.7z', '.001', '.part1.rar')
    FOUR_GB = 4 * 1024 * 1024 * 1024 # 4 GB Sınırı
    
    while not task_queue.empty():
        task = await task_queue.get()
        client, message, channel_id, start_id, end_id = task
        
        status_msg = await message.reply_text(f"⚙️ **İşlem Başlatıldı:** `{channel_id}` (ID: {start_id} - {end_id})")
        
        try:
            # 1. Dosyaları İndirme Aşaması
            downloaded_files = 0
            original_caption = None
            
            for msg_id in range(start_id, end_id + 1):
                try:
                    msg = await client.get_messages(channel_id, msg_id)
                    if msg and msg.document:
                        if not original_caption and msg.caption:
                            original_caption = msg.caption
                            
                        file_name = msg.document.file_name or ""
                        if file_name.lower().endswith(ARCHIVE_EXTENSIONS) or ".part" in file_name.lower():
                            print(f"\n📥 Arşiv İndiriliyor: {file_name}")
                            
                            await client.download_media(
                                message=msg,
                                file_name=os.path.join(DOWNLOAD_DIR, file_name),
                                progress=download_progress
                            )
                            print()
                            downloaded_files += 1
                except Exception as e:
                    LOGGER.error(f"Mesaj {msg_id} çekilirken hata: {e}")

            if downloaded_files == 0:
                await status_msg.edit_text("❌ Belirtilen aralıkta indirilecek arşiv (.zip, .rar vb.) bulunamadı.")
                task_queue.task_done()
                continue

            await status_msg.edit_text("📦 Arşiv dosyaları indirildi. Çıkarma işlemi başlatılıyor...")

            # 2. Unzip / Unrar Aşaması
            first_part_files = []
            for ext in ["*.part1.rar", "*.part01.rar", "*.part1.exe", "*.zip.001", "*.7z.001", "*.rar", "*.zip", "*.7z"]:
                found = glob.glob(os.path.join(DOWNLOAD_DIR, ext))
                if found:
                    first_part_files.extend(found)
                    break

            first_part_files = list(set(first_part_files))
                
            for first_part_path in first_part_files:
                first_part_filename = Path(first_part_path).name
                
                base_name = first_part_filename
                for clean_ext in [".part1.rar", ".part01.rar", ".zip.001", ".7z.001", ".rar", ".zip", ".7z"]:
                    base_name = base_name.replace(clean_ext, "")
                    
                final_output_path_base = UNZIP_PATH / base_name
                final_output_path_base.mkdir(parents=True, exist_ok=True)
                
                command = ["7z", "x", str(first_part_path), f"-o{final_output_path_base}", "-y"]
                process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await process.communicate()

                # Temizlik - Klasördeki arşiv parçalarını sil
                for file_in_dir in os.listdir(DOWNLOAD_DIR):
                    file_p = os.path.join(DOWNLOAD_DIR, file_in_dir)
                    if os.path.isfile(file_p) and file_in_dir.lower().endswith(ARCHIVE_EXTENSIONS):
                        try: os.remove(file_p)
                        except Exception: pass

                # 3. Video Tarama ve İşleme
                video_files = []
                for ext in ["*.mkv", "*.mp4", "*.avi", "*.mov"]:
                    video_files.extend(final_output_path_base.rglob(ext))
                
                for video_file_path in video_files:
                    processed_video = await process_media(str(video_file_path))
                    
                    if processed_video and os.path.exists(processed_video):
                        file_size = os.path.getsize(processed_video)
                        
                        # --- YÜKLEME KOŞULU (4 GB KONTROLÜ) ---
                        if file_size >= FOUR_GB:
                            await message.reply_text(f"ℹ️ Dosya 4 GB üzerinde (`{file_size / (1024**3):.2f} GB`), Streamtape'e yönlendiriliyor...")
                            upload_success = await upload_to_streamtape(client, message.chat.id, processed_video)
                        else:
                            await message.reply_text(f"ℹ️ Dosya 4 GB altında (`{file_size / (1024**2):.2f} MB`), Log Kanalına yükleniyor...")
                            upload_success = await upload(client, processed_video, original_caption)
                        
                        if upload_success:
                            try:
                                if os.path.exists(processed_video): os.remove(processed_video)
                            except Exception as e: LOGGER.error(f"Silme hatası: {e}")
                    else:
                        await message.reply_text(f"❌ Video işlenemedi: `{video_file_path.name}`")

            await status_msg.edit_text("✨ Görev başarıyla tamamlandı.")

        except Exception as e:
            LOGGER.error(f"Kuyruk hatası: {e}", exc_info=True)
            await message.reply_text(f"❌ Görev sırasında hata oluştu: {e}")

        task_queue.task_done()
        
    is_processing = False


# --- KOMUT İŞLEYİCİSİ ---

@app.on_message(filters.me & filters.command("uz"))
async def uz_command_handler(client: Client, message: Message):
    global is_processing
    
    args = message.text.split()
    if len(args) < 4:
        await message.reply_text(
            "⚠️ **Hatalı Kullanım!**\n\n"
            "Doğru Kullanım:\n`/uz <kanali_id> <ilk_id> <son_id>`\n"
            "Örnek:\n`/uz -100123456789 105 110`"
        )
        return
        
    try:
        channel_id = int(args[1]) if args[1].startswith("-") or args[1].isdigit() else args[1]
        start_id = int(args[2])
        end_id = int(args[3])
    except ValueError:
        await message.reply_text("❌ Kanal ID ve Mesaj ID'leri sayısal değer olmalıdır.")
        return

    if start_id > end_id:
        await message.reply_text("❌ `ilk_id` değeri `son_id` değerinden büyük olamaz.")
        return

    await task_queue.put((client, message, channel_id, start_id, end_id))
    qsize = task_queue.qsize()

    if is_processing:
        await message.reply_text(f"⏳ **Sıraya Eklendi!** (Sıra: **{qsize}**)")
    else:
        await message.reply_text("🚀 Görev alındı, terminal üzerinden izleyebilirsiniz...")
        asyncio.create_task(process_queue())

if __name__ == "__main__":
    LOGGER.info("Userbot Başlatılıyor...")
    app.run()
