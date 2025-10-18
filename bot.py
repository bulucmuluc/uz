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

# Gerekli kütüphane: pip install aiohttp
import aiohttp 

# .env dosyasından ortam değişkenlerini yükle
load_dotenv()

# --- YAPILANDIRMA VE SABİTLER ---

try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # Streamtape API Bilgileri
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

# --- İLERLEME VE BOYUT HESAPLAMA FONKSİYONLARI ---

def humanbytes(size):
    """Bayt boyutunu insan tarafından okunabilir formata dönüştürür."""
    if not size: return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power: 
        size /= power
        n += 1
    return f"{round(size, 2)} {Dic_powerN[n]}B"

async def progress_bar(current, total, message, start, prefix="İşlem"):
    """
    Telegram mesajını düzenleyerek bir ilerleme çubuğu gösterir.
    """
    now = time.time()
    diff = now - start
    
    # Her 5 saniyede bir veya işlem bittiğinde mesajı güncelle
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

# --- FFPROBE ve FFMPEG Fonksiyonları ---

async def get_audio_stream_info(path_to_file):
    """Video dosyasındaki ses akışlarını analiz eder ve Türkçe sesin index'ini bulur."""
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
    """Seçilen ses akışını (tercihen Türkçe) video dosyasına gömer ve yeni bir dosya oluşturur."""
    
    if final_audio_index is None:
        return None, "❌ HATA: Dosyada ses akışı bulunamadı."
        
    dir_name = os.path.dirname(path_to_file)
    filename = os.path.splitext(os.path.basename(path_to_file))[0]
    
    # Yeni oluşturulan dosyanın tam yolunu Path objesi ile oluştur
    output_path = Path(dir_name) / f"{filename}-TR.mp4"
    
    cmd_ffmpeg = [
        "ffmpeg", "-i", path_to_file, "-map", "0:v:0", "-map", f"0:{final_audio_index}", 
        "-c", "copy", "-y", str(output_path)           
    ]

    process = await asyncio.create_subprocess_exec(*cmd_ffmpeg, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    
    if process.returncode != 0:
        return None, f"❌ FFMPEG HATA: Ses kopyalama işlemi başarısız oldu. Hata: {stderr.decode('utf-8', errors='ignore')}"
    
    # Başarılı dönüşte oluşturulan dosyanın tam yolunu döndür
    if is_turkish_present:
        return str(output_path), f"✅ **Türkçe Ses** akışı kopyalanıp ayrı bir dosya oluşturuldu: `{output_path.name}`"
    else:
        return str(output_path), f"⚠️ **Türkçe Ses** bulunamadı, mevcut herhangi bir akış ({final_audio_index}) kopyalandı: `{output_path.name}`"

# --- STREAMTAPE YÜKLEME FONKSİYONU ---

async def upload_to_streamtape(client: Client, message: Message, path_to_file: str):
    """
    İşlenmiş dosyayı Streamtape'e yükler ve ilerleme çubuğu gösterir.
    """
    
    # Yüklenecek dosyanın varlığını kontrol et
    if not os.path.exists(path_to_file):
        await message.reply_text(f"❌ Yüklenecek dosya bulunamadı: `{Path(path_to_file).name}`")
        return
        
    a = await message.reply_text("`Streamtape API'ye bağlanılıyor...`", quote=True) 
    file_size = os.path.getsize(path_to_file)
    
    # Asenkron okuma yaparak ilerlemeyi takip eden custom sınıf
    class ProgressFile(object):
        """Dosyayı okurken ilerlemeyi güncelleyen custom reader"""
        def __init__(self, file_path, status_message, total_size):
            self.file_path = file_path
            self.file = open(file_path, 'rb')
            self.total = total_size
            self.uploaded = 0
            self.status_message = status_message
            self.start_time = time.time()
            self.last_update = 0
            
        def __len__(self):
            return self.total

        async def read(self, size):
            chunk = await asyncio.to_thread(self.file.read, size)
            if chunk:
                self.uploaded += len(chunk)
                
                now = time.time()
                if now - self.last_update >= 5 or self.uploaded == self.total:
                    try:
                        await progress_bar(
                            self.uploaded, 
                            self.total, 
                            self.status_message, 
                            self.start_time, 
                            prefix="🚀 Streamtape'e Yükleniyor"
                        )
                    except Exception:
                        pass
                    self.last_update = now
                    
            return chunk
            
        def close(self):
                self.file.close()

    success = False
    
    try:
        async with aiohttp.ClientSession() as session:
            
            # 1. Yükleme URL'sini al
            Main_API = "https://api.streamtape.com/file/ul?login={}&key={}"
            
            await a.edit_text("`Streamtape: Yükleme URL'si talep ediliyor...`")
            hit_api = await session.get(Main_API.format(Config.STREAMTAPE_API_USERNAME, Config.STREAMTAPE_API_PASS))
            
            http_status = hit_api.status
            json_data = await hit_api.json()
            
            if json_data.get("status") != 200:
                await a.edit_text(
                    f"❌ Streamtape API'den Yükleme URL'si alınamadı!\n"
                    f"HTTP Durumu: `{http_status}` | API Durumu: `{json_data.get('status')}`"
                )
                return

            temp_api = json_data["result"]["url"]
            await a.edit_text(f"`Yükleme URL'si alındı (HTTP {http_status}). Yükleme başlıyor...`")
            
            # 2. Dosyayı Yükle
            data = aiohttp.FormData()
            filename = Path(path_to_file).name.replace("_", " ") 
            
            data.add_field(
                'file1',
                ProgressFile(path_to_file, a, file_size),
                filename=filename,
                content_type='application/octet-stream'
            )
            
            response = await session.post(temp_api, data=data)
            
            upload_http_status = response.status
            
            try:
                data_f = await response.json(content_type=None)
            except aiohttp.ContentTypeError:
                data_f = {} 

            status = data_f.get("status")
            download_link = data_f.get("result", {}).get("url")
            
            if int(status) != 200 or not download_link:
                error_msg = data_f.get("msg", "Bilinmeyen API Hatası.")
                await a.edit_text(
                    f"❌ Dosya Streamtape'e yüklenirken hata oluştu!\n"
                    f"HTTP Durumu: `{upload_http_status}`\nAPI Durumu: `{status}`\nMesaj: `{error_msg}`"
                )
                return

            # 3. Başarılı Sonuçları İşle
            success = True
            await message.reply_text(
                f"**Yükleme Başarılı!** (HTTP `{upload_http_status}`, API `{status}`)\n"
                f"**Dosya Adı:** `{filename}`\n\n**İndirme Linki:** `{download_link}`",
                parse_mode="markdown",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Link'i Aç", url=download_link)],
                        [InlineKeyboardButton("Dosyayı Sil", callback_data=f"deletestream_{download_link}")] 
                    ]
                )
            )
            
    except Exception as e:
        await a.edit_text(f"❌ Streamtape Yükleme Sırasında Beklenmedik Hata: `{e}`")
        
    finally:
        # Son dosyayı (-TR.mp4) yükleme başarısız olsa bile sil
        if os.path.exists(path_to_file):
            try:
                os.remove(path_to_file)
                if success:
                    await message.reply_text(f"🗑️ Yükleme sonrası son dosya silindi: `{Path(path_to_file).name}`")
            except Exception as e:
                 await message.reply_text(f"⚠️ Son dosya silinemedi: {e}")
                 
        await a.delete() # 'Yükleniyor' mesajını sil

# --- PYROGRAM KOMUT İŞLEYİCİLERİ ---

@app.on_message(filters.document & filters.private)
async def handle_document(client: Client, message: Message):
    """Kullanıcıdan gelen parçalı ZIP dosyalarını indirir."""
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
    """/uz komutu ile çıkarma işlemini tetikler, ses işler ve Streamtape'e yükler."""
    await message.reply_text("🔍 ZIP çıkarma işlemi başlatılıyor...")

    first_part_files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.zip.001"))
    
    if not first_part_files:
        await message.reply_text("❌ HATA: `.zip.001` dosyası bulunamadı.")
        return

    await message.reply_text(f"Toplam **{len(first_part_files)}** adet dosya albümü bulundu. İşlem başlıyor...")

    for first_part_path in first_part_files:
        first_part_filename = Path(first_part_path).name
        base_name = first_part_filename.replace(".zip.001", "").replace(".zip", "")
        final_output_path_base = UNZIP_PATH / base_name
        final_output_path_base.mkdir(parents=True, exist_ok=True)
        
        status_msg = await message.reply_text(f"\n--- **{base_name}** albümü için çıkarma başlatılıyor. ---")
        
        # 7z komutu
        command = [
            "7z", 
            "e", 
            str(Path(DOWNLOAD_DIR) / first_part_filename), 
            f"-o{final_output_path_base}", 
            "-y"
        ]
        
        log_content = f"**7z Çıktı Akışı (Log):**\n```\n"
        
        try:
            # 7z çıkarma işleminin gerçek zamanlı log akışı
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, 
                cwd=None
            )
            
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                    
                decoded_line = line.decode('utf-8', errors='ignore').strip()
                if decoded_line:
                    # Log içeriğini limitler
                    log_content += decoded_line + "\n"
                    if len(log_content) > 3500: 
                        log_content = log_content[-3500:] 
                        log_content = log_content[log_content.find('\n')+1:] 
                        
                    try:
                        # Log mesajını her yeni satırda güncelle
                        await status_msg.edit_text(log_content + "\n```", parse_mode="markdown")
                    except Exception:
                        pass 

            # İşlemin bitmesini bekle
            returncode = await process.wait()
            
            if returncode != 0:
                 raise subprocess.CalledProcessError(returncode, command, stdout=log_content.encode(), stderr=b'')

            # Başarı mesajı
            final_log_msg = f"✅ **{base_name}** ZIP çıkarma işlemi tamamlandı!\n\n" + log_content + "\n```"
            await status_msg.edit_text(final_log_msg, parse_mode="markdown")

            # --- ZIP SİLME İŞLEMİ (Temizlik) ---
            try:
                base_zip_name = first_part_filename.replace(".001", "")
                for part_file in glob.glob(os.path.join(DOWNLOAD_DIR, f"{base_zip_name}*")):
                    os.remove(part_file)
                await message.reply_text(f"🗑️ `{base_name}` albümüne ait tüm ZIP parçaları sunucudan silindi.")
            except Exception as e:
                await message.reply_text(f"Uyarı: Parçalar silinirken hata oluştu: {e}")

            # --- KRİTİK KONTROL: Klasör Boş Mu? ---
            if not any(final_output_path_base.iterdir()):
                await message.reply_text(
                    f"❌ KRİTİK HATA: `{final_output_path_base.name}` klasörü **boş** çıktı. Video arama atlanıyor."
                )
                continue 
            
            # --- SES İŞLEME VE YÜKLEME KISMI ---
            
            video_files = []
            for ext in ["*.mkv", "*.mp4", "*.avi", "*.mov"]:
                # Recursive=True ile tüm alt dizinler taranıyor
                video_files.extend(glob.glob(str(final_output_path_base / "**" / ext), recursive=True))
            
            if not video_files:
                await message.reply_text(
                    f"⚠️ `{final_output_path_base.name}` klasörünün alt klasörlerinde video dosyası bulunamadı. Yükleme atlandı."
                )
            
            # Bulunan her video dosyasını işler
            for video_file in video_files:
                video_file_path = Path(video_file)
                
                # Hata Ayıklama Adımı 1: Ses Analizi Başlangıcı
                await message.reply_text(f"🎵 Video bulundu: `{video_file_path.name}`. Ses akışı analiz ediliyor...")
                
                final_audio_index, is_turkish_present = await get_audio_stream_info(str(video_file_path))
                
                if final_audio_index is not None:
                    
                    # Hata Ayıklama Adımı 2: Ses İşleme Başlangıcı
                    await message.reply_text(f"🔊 Ses akışı (Index {final_audio_index}) ile FFMPEG işlemi başlatılıyor...")
                    
                    # new_file_path burada -TR.mp4 dosyasının yolunu alır
                    new_file_path, result_msg = await process_audio_only(str(video_file_path), final_audio_index, is_turkish_present)
                    await message.reply_text(result_msg)
                    
                    # --- Hata Ayıklama Adımı 3: Streamtape Kontrolü (KRİTİK BÖLGE) ---
                    if new_file_path and os.path.exists(new_file_path):
                        await message.reply_text("✅ FFMPEG başarılı ve dosya (`-TR.mp4`) bulundu! Streamtape yüklemesi çağrılıyor...")
                        # Streamtape yüklemesini çağır ve await ile bitmesini bekle
                        await upload_to_streamtape(client, message, new_file_path) 
                    else:
                        # new_file_path None döndüyse veya dosya oluşmadıysa (FFMPEG hatası)
                        await message.reply_text("❌ FFMPEG işlemi başarısız oldu veya `-TR.mp4` dosyası oluşmadı. Streamtape yüklemesi atlanıyor.")
                        
                    # Yükleme sonrası Orijinal video dosyasını sil
                    try: 
                        os.remove(video_file_path)
                        await message.reply_text(f"🗑️ Orijinal video dosyası silindi: `{video_file_path.name}`")
                    except Exception: 
                        pass
                        
                else:
                    await message.reply_text("❌ Ses akışı bilgisi alınamadı, FFMPEG/Streamtape işlemi atlanıyor.")

        except subprocess.CalledProcessError as e:
            error_message = f"❌ HATA: **{base_name}** çıkarma işlemi başarısız oldu! Hata Kodu: {e.returncode}\n"
            error_message += f"7z Hata Çıktısı (Son Kısımlar): \n```\n{log_content[-1000:]}...\n```"
            await status_msg.edit_text(error_message, parse_mode="markdown")

        except FileNotFoundError as e:
            await message.reply_text(f"❌ KRİTİK HATA: {e} komutu bulunamadı. Lütfen 7z/ffmpeg kurun.")

    await message.reply_text("\n🎉 Tüm işlemler tamamlandı.")

# --- CALLBACK QUERY HANDLER ---

@app.on_callback_query()
async def callback_handler(client: Client, cb):
    """Streamtape silme callback'ini işler."""
    
    if cb.data.startswith("deletestream_"):
        await cb.answer("Silme işlemi başlatılıyor...")
        
        download_link = cb.data.split("deletestream_", 1)[1] 
        # Streamtape linkinden dosya tokenini çıkarır
        token = download_link.split("/v/", 1)[-1].split("?", 1)[0]
        
        async with aiohttp.ClientSession() as session:
            del_api = "[https://api.streamtape.com/file/delete?login=](https://api.streamtape.com/file/delete?login=){}&key={}&file={}"
            data_f = await session.get(
                del_api.format(Config.STREAMTAPE_API_USERNAME, Config.STREAMTAPE_API_PASS, token))
            json_data = await data_f.json()
            
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
