import os
import subprocess
import json
import asyncio
import time
import math
import glob
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

# .env dosyasından ortam değişkenlerini yükle
load_dotenv()

# --- YAPILANDIRMA VE SABİTLER ---

try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
except (TypeError, ValueError):
    print("HATA: Lütfen .env dosyasındaki API_ID, API_HASH veya BOT_TOKEN değerlerini kontrol edin.")
    exit()

# İndirme ve çıkarma işleminin yapılacağı ana dizin
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
UNZIP_SUBDIR = "unzip" # Çıkarılan son dosyaların kaydedileceği alt klasör adı

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
    # 'B' harfini eklerken string formatını düzelt
    return f"{round(size, 2)} {Dic_powerN[n]}B"

async def progress_bar(current, total, message, start, prefix="İşlem"):
    """
    Telegram mesajını düzenleyerek bir ilerleme çubuğu gösterir.
    Pyrogram'ın 'progress' parametresi ile uyumlu olarak 'message' ve 'start' değerlerini alır.
    """
    now = time.time()
    diff = now - start
    
    # Her 5 saniyede bir veya işlem bittiğinde mesajı güncelle
    # NOT: Pyrogram, kritik güncellemeleri kendi içinde zaten hızlandırır.
    if round(diff % 5) == 0 or current == total:
        if diff == 0: diff = 1 # Sıfıra bölme hatasını önle
        
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
        
        # Güncellenen metin
        text = f"**{prefix}**\n\n{progress}\n**Durum:** {humanbytes(current)} / {humanbytes(total)}\n**Hız:** {humanbytes(speed)}/s\n**Kalan Süre (ETA):** {eta}s"
        
        try: 
            await message.edit_text(text)
        except Exception: 
            pass # Sık güncelleme hatalarını yoksay

# --- FFPROBE ve FFMPEG Fonksiyonları ---

async def get_audio_stream_info(path_to_file):
    """
    Video dosyasındaki ses akışlarını analiz eder ve Türkçe sesin index'ini bulur.
    """
    cmd_probe = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path_to_file]
    
    # Asenkron olarak subprocess çalıştırma
    process = await asyncio.create_subprocess_exec(
        *cmd_probe,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    try:
        video_info = json.loads(stdout.decode('utf-8'))
    except json.JSONDecodeError:
        print(f"HATA: FFprobe çıktısı JSON olarak çözülemedi: {stderr.decode('utf-8')}")
        return None, False

    audio_streams = [s for s in video_info.get("streams", []) if s.get("codec_type") == "audio"]
    turkish_stream_index = None
    any_stream_index = None
    
    for stream in audio_streams:
        if any_stream_index is None: any_stream_index = stream["index"]
            
        lang = stream.get("tags", {}).get("language", "").lower()
        title = stream.get("tags", {}).get("title", "").lower()
        
        # Türkçe akışı kontrol et
        if lang in ["tur", "trk", "turkish"] or "türkçe" in title:
            turkish_stream_index = stream["index"]
            break
            
    final_audio_index = turkish_stream_index if turkish_stream_index is not None else any_stream_index
    return final_audio_index, turkish_stream_index is not None


async def process_audio_only(path_to_file, final_audio_index, is_turkish_present):
    """
    Seçilen ses akışını (tercihen Türkçe) video dosyasına gömer ve yeni bir dosya oluşturur.
    """
    
    if final_audio_index is None:
        return "❌ HATA: Dosyada ses akışı bulunamadı."
        
    dir_name = os.path.dirname(path_to_file)
    filename = os.path.splitext(os.path.basename(path_to_file))[0]
    
    # Yeni dosya adı formatı: [ORIJINAL_AD]-TR.mp4
    output_path = Path(dir_name) / f"{filename}-TR.mp4"
    
    # FFmpeg komutu: Ses ve video akışlarını yeniden kodlama yapmadan kopyala.
    cmd_ffmpeg = [
        "ffmpeg",
        "-i", path_to_file,        # Giriş dosyası
        "-map", "0:v:0",           # Birinci video akışını al
        "-map", f"0:{final_audio_index}", # Seçilen ses akışını al
        "-c", "copy",              # Hızlı kopyalama
        "-y",                      # Üzerine yaz
        str(output_path)           # Çıktı dosyası
    ]

    # Asenkron olarak subprocess çalıştırma
    process = await asyncio.create_subprocess_exec(
        *cmd_ffmpeg,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    
    if process.returncode != 0:
        return f"❌ FFMPEG HATA: Ses kopyalama işlemi başarısız oldu. Hata: {stderr.decode('utf-8', errors='ignore')}"
    
    # Başarılı mesajı
    if is_turkish_present:
        return f"✅ **Türkçe Ses** akışı kopyalanıp ayrı bir dosya oluşturuldu: `{output_path.name}`"
    else:
        return f"⚠️ **Türkçe Ses** bulunamadı, mevcut herhangi bir akış ({final_audio_index}) kopyalandı: `{output_path.name}`"

# --- PYROGRAM KOMUT İŞLEYİCİLERİ ---

@app.on_message(filters.document & filters.private)
async def handle_document(client: Client, message: Message):
    """
    Kullanıcıdan gelen parçalı ZIP dosyalarını sunucuya indirir ve ilerleme çubuğu gösterir.
    """
    file_name = message.document.file_name
    
    if file_name and ".zip.00" in file_name:
        # İlerleme mesajını oluştur ve zaman damgasını kaydet
        status_message = await message.reply_text(f"`{file_name}`: İndiriliyor... Lütfen bekleyin.")
        start_time = time.time()
        
        try:
            # Pyrogram'ın 'download' metodunu progress ve progress_args ile kullanma
            download_path = await message.download(
                file_name=os.path.join(DOWNLOAD_DIR, file_name),
                progress=progress_bar, 
                progress_args=(status_message, start_time, f"**{Path(file_name).name}** İndiriliyor")
            )
            
            # Başarılı indirme sonrası mesajı düzenle
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
    /uz komutu ile çıkarma işlemini tetikler ve çıkan video dosyalarında ses işleme başlatır.
    """
    await message.reply_text("🔍 ZIP çıkarma işlemi başlatılıyor. `DOWNLOAD_DIR` içindeki tüm `.zip.001` dosyaları taranıyor...")

    # İndirme klasöründeki tüm .zip.001 dosyalarını bul
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
        
        # 7z çıkarma komutu
        command = ["7z", "x", first_part_path, f"-o{final_output_path_base}", "-y"]
        
        try:
            # Senkron subprocess (7z genellikle hızlı çalışır, ama büyük dosyalarda botu bloke edebilir)
            process = subprocess.run(command, cwd=DOWNLOAD_DIR, capture_output=True, text=True, timeout=None, check=True) 
            await status_msg.edit_text(f"✅ **{base_name}** ZIP çıkarma işlemi tamamlandı!")
            
            # --- SES İŞLEME KISMI ---
            
            # Çıkarılan video dosyalarını bul (.mkv, .mp4, .avi vb.)
            video_files = []
            for ext in ["*.mkv", "*.mp4", "*.avi", "*.mov"]:
                video_files.extend(glob.glob(os.path.join(final_output_path_base, "**", ext), recursive=True))

            if not video_files:
                await message.reply_text(f"⚠️ `{base_name}` klasöründe video dosyası bulunamadı. Ses işlemi atlanıyor.")
            
            for video_file in video_files:
                await message.reply_text(f"🎵 Video bulundu: `{Path(video_file).name}`. Ses akışı analiz ediliyor...")
                
                # 1. Ses akışı bilgilerini al
                final_audio_index, is_turkish_present = await get_audio_stream_info(video_file)
                
                if final_audio_index is None:
                    await message.reply_text("❌ Ses akışı bilgisi alınamadı, işlem atlanıyor.")
                    continue
                
                # 2. Ses akışını kopyala ve yeni dosya oluştur
                result_msg = await process_audio_only(video_file, final_audio_index, is_turkish_present)
                await message.reply_text(result_msg)
            
            # --- SİLME İŞLEMİ ---
            try:
                base_zip_name = first_part_filename.replace(".001", "")
                for part_file in glob.glob(os.path.join(DOWNLOAD_DIR, f"{base_zip_name}*")):
                    os.remove(part_file)
                await message.reply_text(f"🗑️ `{base_name}` albümüne ait tüm ZIP parçaları sunucudan silindi.")
            except Exception as e:
                await message.reply_text(f"Uyarı: Parçalar silinirken hata oluştu: {e}")

        except subprocess.CalledProcessError as e:
            error_message = f"❌ HATA: **{base_name}** çıkarma işlemi başarısız oldu! Hata Kodu: {e.returncode}"
            await status_msg.edit_text(error_message)

        except FileNotFoundError as e:
            await message.reply_text(f"❌ KRİTİK HATA: {e} komutu bulunamadı. Lütfen 7z/ffmpeg kurun.")

    await message.reply_text("\n🎉 Tüm dosya albümleri ve ses işleme işlemleri tamamlandı.")


# --- DİĞER KOMUTLAR ---

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "Merhaba! Ben Pyrogram ZIP Birleştirme Botuyum.\n"
        "1. Lütfen sırayla parçalı ZIP dosyalarını (örn: `.zip.001`, `.zip.002`...) gönderin.\n"
        "2. **Tüm parçaları gönderdikten sonra** sadece **`/uz`** komutunu kullanın.\n"
        "Bot, ZIP'leri açacak ve çıkan videolardaki Türkçe sesi bulup yeni bir dosyaya kaydedecektir."
    )

# Botu çalıştır
if __name__ == "__main__":
    print(f"Bot Başlatılıyor... İndirme Dizini: {DOWNLOAD_DIR}")
    app.run()
      
