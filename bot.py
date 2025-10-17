import os
import subprocess
import json
import asyncio
import time
import math
import glob
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# Yeni gereksinim
import aiohttp 

# .env dosyasından ortam değişkenlerini yükle
load_dotenv()

# --- YAPILANDIRMA VE SABİTLER ---

try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # Yeni Gereksinim: Streamtape API Bilgileri (ENV'den okunduğu varsayılır)
    class Config:
        STREAMTAPE_API_USERNAME = os.getenv("STREAMTAPE_API_USERNAME")
        STREAMTAPE_API_PASS = os.getenv("STREAMTAPE_API_PASS")
        
    if not Config.STREAMTAPE_API_USERNAME or not Config.STREAMTAPE_API_PASS:
        raise ValueError("Streamtape API bilgileri eksik.")

except (TypeError, ValueError) as e:
    print(f"HATA: Yapılandırma hatası: {e}")
    exit()

# İndirme ve çıkarma işleminin yapılacağı ana dizin
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
UNZIP_SUBDIR = "unzip" 

# Klasörleri oluştur
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
UNZIP_PATH = Path(DOWNLOAD_DIR) / UNZIP_SUBDIR
UNZIP_PATH.mkdir(exist_ok=True)

# Pyrogram İstemcisi
app = Client(
    "zip_unpacker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- İLERLEME VE DİĞER FONKSİYONLAR (ÖNCEKİ YANITLARDAN) ---

def humanbytes(size):
    # ... (humanbytes fonksiyonu buraya kopyalanacak) ...
    if not size: return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power: 
        size /= power
        n += 1
    return f"{round(size, 2)} {Dic_powerN[n]}B"

async def progress_bar(current, total, message, start, prefix="İşlem"):
    # ... (progress_bar fonksiyonu buraya kopyalanacak) ...
    now = time.time()
    diff = now - start
    
    if round(diff % 5) == 0 or current == total:
        if diff == 0: diff = 1 
        
        percentage = current * 100 / total
        speed = current / diff
        
        try:
            eta = round((total - current) / speed)
        except ZeroDivisionError:
            eta = 0
        
        bar_length = 10
        filled_length = math.floor(percentage / 100 * bar_length)
        bar = '🟢' * filled_length + '⚪' * (bar_length - filled_length)
        progress = f"[{bar}] {round(percentage, 2)}%"
        
        text = f"**{prefix}**\n\n{progress}\n**Durum:** {humanbytes(current)} / {humanbytes(total)}\n**Hız:** {humanbytes(speed)}/s\n**Kalan Süre (ETA):** {eta}s"
        
        try: 
            await message.edit_text(text)
        except Exception: 
            pass 

async def get_audio_stream_info(path_to_file):
    # ... (get_audio_stream_info fonksiyonu buraya kopyalanacak) ...
    cmd_probe = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path_to_file]
    
    process = await asyncio.create_subprocess_exec(*cmd_probe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    
    try:
        video_info = json.loads(stdout.decode('utf-8'))
    except json.JSONDecodeError:
        return None, False

    audio_streams = [s for s in video_info.get("streams", []) if s.get("codec_type") == "audio"]
    turkish_stream_index = None
    any_stream_index = None
    
    for stream in audio_streams:
        if any_stream_index is None: any_stream_index = stream["index"]
            
        lang = stream.get("tags", {}).get("language", "").lower()
        title = stream.get("tags", {}).get("title", "").lower()
        
        if lang in ["tur", "trk", "turkish"] or "türkçe" in title:
            turkish_stream_index = stream["index"]
            break
            
    final_audio_index = turkish_stream_index if turkish_stream_index is not None else any_stream_index
    return final_audio_index, turkish_stream_index is not None

async def process_audio_only(path_to_file, final_audio_index, is_turkish_present):
    # ... (process_audio_only fonksiyonu buraya kopyalanacak) ...
    if final_audio_index is None:
        return None, "❌ HATA: Dosyada ses akışı bulunamadı."
        
    dir_name = os.path.dirname(path_to_file)
    filename = os.path.splitext(os.path.basename(path_to_file))[0]
    
    output_path = Path(dir_name) / f"{filename}-TR.mp4"
    
    cmd_ffmpeg = [
        "ffmpeg",
        "-i", path_to_file,        
        "-map", "0:v:0",           
        "-map", f"0:{final_audio_index}", 
        "-c", "copy",              
        "-y",                      
        str(output_path)           
    ]

    process = await asyncio.create_subprocess_exec(*cmd_ffmpeg, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    
    if process.returncode != 0:
        return None, f"❌ FFMPEG HATA: Ses kopyalama işlemi başarısız oldu. Hata: {stderr.decode('utf-8', errors='ignore')}"
    
    if is_turkish_present:
        return str(output_path), f"✅ **Türkçe Ses** akışı kopyalanıp ayrı bir dosya oluşturuldu: `{output_path.name}`"
    else:
        return str(output_path), f"⚠️ **Türkçe Ses** bulunamadı, mevcut herhangi bir akış ({final_audio_index}) kopyalandı: `{output_path.name}`"

# --- STREAMTAPE YÜKLEME FONKSİYONU ---

async def upload_to_streamtape(client: Client, message: Message, path_to_file: str):
    """
    İşlenmiş dosyayı Streamtape'e yükler ve linki döndürür.
    :param path_to_file: Yüklenecek dosyanın tam yolu.
    """
    
    # Yükleniyor mesajı (a) oluşturuluyor
    a = await message.reply_text("`Yükleniyor...`", quote=True) 
    
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Yükleme URL'sini al
            Main_API = "https://api.streamtape.com/file/ul?login={}&key={}"
            hit_api = await session.get(Main_API.format(Config.STREAMTAPE_API_USERNAME, Config.STREAMTAPE_API_PASS))
            json_data = await hit_api.json()
            
            if json_data["status"] != 200:
                await a.edit_text("❌ Streamtape API'den Yükleme URL'si alınamadı.")
                return

            temp_api = json_data["result"]["url"]
            
            # 2. Dosyayı yükle
            # Dosyayı Path nesnesinden string'e çeviriyoruz
            filename = Path(path_to_file).name.replace("_", " ") 
            
            with open(path_to_file, 'rb') as f:
                files = {'file1': f}
                response = await session.post(temp_api, data=files)
                data_f = await response.json(content_type=None)
            
            status = data_f["status"]
            
            if int(status) != 200:
                await a.edit_text("❌ Dosya Streamtape'e yüklenirken hata oluştu.")
                return

            # 3. Sonuçları işle
            download_link = data_f["result"]["url"]
            
            # Başarılı mesajı gönder
            await message.reply_text(
                f"**Dosya Adı:** `{filename}`\n\n**İndirme Linki:** `{download_link}`",
                parse_mode="markdown",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Link'i Aç", url=download_link)],
                        # Callback Data Mantığı: Sizin kodunuzdan alındı, Pyrogram'da 'cb' yerine 'c' ve 'message' kullanılır
                        [InlineKeyboardButton("Dosyayı Sil", callback_data=f"deletestream_{download_link}")] 
                    ]
                )
            )
            
    except Exception as e:
        await a.edit_text(f"❌ Streamtape Yükleme Sırasında Beklenmedik Hata: {e}")
        
    finally:
        # Sunucudaki yüklü dosyayı sil (Başarılı veya başarısız olsun)
        try:
            os.remove(path_to_file)
        except:
            pass
        
        # 'Yükleniyor' mesajını sil
        await a.delete()

# --- PYROGRAM KOMUT İŞLEYİCİLERİ ---

@app.on_message(filters.document & filters.private)
async def handle_document(client: Client, message: Message):
    # ... (handle_document kısmı değişmedi) ...
    file_name = message.document.file_name
    
    if file_name and ".zip.00" in file_name:
        status_message = await message.reply_text(f"`{file_name}`: İndiriliyor... Lütfen bekleyin.")
        start_time = time.time()
        
        try:
            download_path = await message.download(
                file_name=os.path.join(DOWNLOAD_DIR, file_name),
                progress=progress_bar, 
                progress_args=(status_message, start_time, f"**{Path(file_name).name}** İndiriliyor")
            )
            
            await status_message.edit_text(
                f"✅ Parça başarıyla indirildi: `{Path(download_path).name}`\n"
                f"Tüm parçaları gönderdikten sonra **`/uz`** komutunu kullanın."
            )
        except Exception as e:
            await status_message.edit_text(f"❌ İndirme sırasında bir hata oluştu: {e}")
            
    else:
        await message.reply_text("Bu dosya bir parçalı ZIP (.zip.00x) dosyası gibi görünmüyor.")


@app.on_message(filters.command("uz") & filters.private)
async def uz_command(client: Client, message: Message):
    """
    /uz komutu ile çıkarma işlemini tetikler, ses işler ve Streamtape'e yükler.
    """
    await message.reply_text("🔍 ZIP çıkarma işlemi başlatılıyor...")

    first_part_files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.zip.001"))
    
    if not first_part_files:
        await message.reply_text("❌ HATA: `.zip.001` dosyası bulunamadı.")
        return

    await message.reply_text(f"Toplam **{len(first_part_files)}** adet dosya albümü bulundu. İşlem başlıyor...")

    for first_part_path in first_part_files:
        # ... (ZIP çıkarma mantığı) ...
        first_part_filename = Path(first_part_path).name
        base_name = first_part_filename.replace(".zip.001", "").replace(".zip", "")
        final_output_path_base = UNZIP_PATH / base_name
        final_output_path_base.mkdir(parents=True, exist_ok=True)
        
        status_msg = await message.reply_text(f"\n--- **{base_name}** albümü için çıkarma başlatılıyor. ---")
        
        command = [
            "7z", 
            "e", # Düzeltme: 'x' yerine 'e' (Extract - İç klasör yapısını yoksayar)
            Path(first_part_path).name, 
            f"-o{final_output_path_base}", 
            "-y"
        ]
        
        try:
            await asyncio.to_thread(
                subprocess.run, 
                command, 
                cwd=DOWNLOAD_DIR, 
                capture_output=True, 
                text=True, 
                timeout=None, 
                check=True
            )
            await status_msg.edit_text(f"✅ **{base_name}** ZIP çıkarma işlemi tamamlandı!")
            
            # --- SES İŞLEME KISMI ---
            
            video_files = []
            for ext in ["*.mkv", "*.mp4", "*.avi", "*.mov"]:
                video_files.extend(glob.glob(str(final_output_path_base / ext), recursive=False))
            
            # Sadece tek bir ana video dosyası varsa işler
            if len(video_files) == 1:
                video_file = video_files[0]
                await message.reply_text(f"🎵 Ses akışı analiz ediliyor...")
                
                final_audio_index, is_turkish_present = await get_audio_stream_info(video_file)
                
                if final_audio_index is not None:
                    # Yeni dosyanın yolunu ve sonucu alır
                    new_file_path, result_msg = await process_audio_only(video_file, final_audio_index, is_turkish_present)
                    await message.reply_text(result_msg)
                    
                    # --- YÜKLEME KISMI ---
                    if new_file_path:
                        await upload_to_streamtape(client, message, new_file_path)
                    
                    # Orijinal video dosyasını sil (Streamtape'e yüklendikten sonra)
                    try: os.remove(video_file)
                    except: pass
                        
                else:
                    await message.reply_text("❌ Ses akışı bilgisi alınamadı, işlem atlanıyor.")
            else:
                 await message.reply_text("⚠️ Klasörde birden fazla veya hiç video dosyası bulunamadı. Yükleme atlandı.")
            
            # --- ZİP SİLME İŞLEMİ ---
            try:
                base_zip_name = first_part_filename.replace(".001", "")
                for part_file in glob.glob(os.path.join(DOWNLOAD_DIR, f"{base_zip_name}*")):
                    os.remove(part_file)
                await message.reply_text(f"🗑️ `{base_name}` albümüne ait tüm ZIP parçaları sunucudan silindi.")
            except Exception as e:
                await message.reply_text(f"Uyarı: Parçalar silinirken hata oluştu: {e}")

        except subprocess.CalledProcessError as e:
            error_message = f"❌ HATA: **{base_name}** çıkarma işlemi başarısız oldu! Hata Kodu: {e.returncode}\n"
            error_message += f"7z Hata Çıktısı: `{e.stderr}`\n"
            await status_msg.edit_text(error_message)

        except FileNotFoundError as e:
            await message.reply_text(f"❌ KRİTİK HATA: {e} komutu bulunamadı. Lütfen 7z/ffmpeg kurun.")

    await message.reply_text("\n🎉 Tüm işlemler tamamlandı.")

# --- CALLBACK QUERY HANDLER (SADECE SİLME İÇİN GEREKLİ KISIM) ---

@app.on_callback_query()
async def callback_handler(client: Client, cb):
    """
    Streamtape silme callback'ini işler.
    """
    if cb.data.startswith("deletestream_"):
        await cb.answer("Silme işlemi başlatılıyor...")
        
        # Linki callback datadan çekiyoruz
        download_link = cb.data.split("deletestream_", 1)[1] 
        # Token'ı URL'den alıyoruz (Streamtape linki http://stape.fun/v/TOKEN gibi varsayıldı)
        token = download_link.split("/v/", 1)[-1].split("?", 1)[0]
        
        async with aiohttp.ClientSession() as session:
            del_api = "https://api.streamtape.com/file/delete?login={}&key={}&file={}"
            data_f = await session.get(
                del_api.format(Config.STREAMTAPE_API_USERNAME, Config.STREAMTAPE_API_PASS, token))
            json_data = await data_f.json()
            
            # Streamtape "msg" veya "status" ile cevap verebilir
            status = json_data.get('msg', json_data.get('status'))
            
            if status == "OK" or status == 200:
                await cb.message.edit_text(f"✅ Dosya başarıyla Silindi: `{token}`")
                await client.send_message(
                    chat_id=cb.message.chat.id,
                    text=f"#STREAMTAPE_DELETE:\n\n[{cb.from_user.first_name}](tg://user?id={cb.from_user.id}) Deleted {download_link}",
                    parse_mode="markdown", disable_web_page_preview=True
                )
            else:
                await cb.message.edit_text(f"❌ Dosya Silinemedi! Durum: {status}")

    elif cb.data == "close": 
        await cb.message.delete()  
        await cb.answer("İptal Edildi...", show_alert=True)
        
    # Diğer callback'ler buraya eklenebilir. (Sizinkiler eksik bağımlılıklara sahip olduğu için atlandı.)


# --- BAŞLANGIÇ KOMUTU ---

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "Merhaba! Ben Pyrogram ZIP Birleştirme Botuyum.\n"
        "1. Lütfen sırayla parçalı ZIP dosyalarını (örn: `.zip.001`, `.zip.002`...) gönderin.\n"
        "2. **Tüm parçaları gönderdikten sonra** sadece **`/uz`** komutunu kullanın.\n"
        "Bot, ZIP'leri açacak, çıkan videolardaki Türkçe sesi bulup **Streamtape'e yükleyecektir**."
    )

# Botu çalıştır
if __name__ == "__main__":
    print(f"Bot Başlatılıyor... İndirme Dizini: {DOWNLOAD_DIR}")
    app.run()
