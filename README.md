# Viral Clipper

Video yükle → AI (Gemini) en çok patlayacak anları seçer → ffmpeg klipleri kesip
altyazı yakar → beğendiklerini tek tıkla YouTube'a yayınla.

## Nasıl çalışıyor

1. **Transkript**: `faster-whisper` videonun sesini yerelde (kendi sunucunda,
   API anahtarı gerekmez) metne çevirir, zaman damgalarıyla.
2. **Seçim**: Transkript Gemini'ye gönderilir, "en iyi N kısa an"ı (başlangıç/bitiş,
   başlık, açıklama, neden viral olacağı) JSON olarak ister.
3. **Kesim + altyazı**: Her segment için `ffmpeg` klibi keser, `.srt` altyazı
   dosyası oluşturur ve videoya yakar (burned-in subtitles).
4. **İnceleme**: Klipler tarayıcıda oynatılabilir kartlar halinde gösterilir.
5. **Yayın**: "YouTube'a Yayınla" butonuna basınca, o klip YouTube Data API v3
   ile hesabına yüklenir (OAuth ile bir kere bağlanman gerekiyor).

## En kolay yol: Railway'de deploy (telefondan da yapılabilir)

1. **GitHub'a yükle**: github.com/new ile yeni bir repo aç, bu klasördeki
   dosyaları (zip'i açıp) sürükle-bırak yükle. `.gitignore` sayesinde
   `.env`, `client_secret.json` gibi gizli dosyalar yanlışlıkla yüklenmez.
2. **Railway'e bağlan**: railway.app → GitHub ile giriş yap → **New Project
   → Deploy from GitHub repo** → az önce oluşturduğun repoyu seç.
   `nixpacks.toml` sayesinde ffmpeg otomatik kurulur, `Procfile` başlatma
   komutunu verir.
3. **Environment variables** (Railway panelinde **Variables** sekmesi):
   - `GEMINI_API_KEY` — aistudio.google.com/apikey
   - `FLASK_SECRET_KEY` — rastgele bir metin
   - `GOOGLE_CLIENT_SECRET_JSON` — aşağıdaki adım 4'te indireceğin JSON
     dosyasının **tüm içeriğini** buraya yapıştır (dosya olarak değil)
4. **Google Cloud OAuth kurulumu** (YouTube'a yükleyebilmek için, bir kere):
   - console.cloud.google.com'da yeni proje oluştur
   - **APIs & Services → Library** → **YouTube Data API v3**'ü etkinleştir
   - **OAuth consent screen**: "External" seç, uygulama adı gir, **Test
     users** kısmına kendi YouTube hesabının Google e-postasını ekle
   - **Credentials → Create Credentials → OAuth client ID** → Web application
   - Authorized redirect URI: `https://<railway-domain-adresin>/auth/youtube/callback`
     (Railway sana bir `*.up.railway.app` adresi verecek, onu kullan)
   - "Download JSON" de, açıp içeriğini kopyala, yukarıdaki
     `GOOGLE_CLIENT_SECRET_JSON` değişkenine yapıştır
5. Deploy tamamlanınca Railway'in verdiği linki aç, sağ üstten YouTube
   hesabını bağla, video yüklemeye başla.

---

## Kendi bilgisayarında/sunucunda çalıştırmak istersen

### 1. Sistem bağımlılıkları

```bash
# ffmpeg gerekli — video kesme ve altyazı yakma için
sudo apt install ffmpeg      # Ubuntu/Debian
brew install ffmpeg          # macOS
```

### 2. Python bağımlılıkları

```bash
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Gemini API anahtarı

- https://aistudio.google.com/apikey adresinden ücretsiz bir anahtar al.
- `.env.example` dosyasını `.env` olarak kopyala, `GEMINI_API_KEY` alanını doldur.
- **Not**: Anahtar yoksa sistem çökmez, videoyu eşit parçalara böler (AI seçimi
  olmadan) — test için yeterli ama gerçek kullanım için anahtar şart.

### 4. YouTube'a yükleme için Google Cloud OAuth kurulumu

Bu adım biraz uzun ama bir kere yapılıyor:

1. https://console.cloud.google.com/ üzerinde yeni bir proje oluştur.
2. **APIs & Services → Library** içinden **YouTube Data API v3**'ü etkinleştir.
3. **APIs & Services → OAuth consent screen**:
   - "External" seç, uygulama adı gir.
   - **Test users** kısmına kendi YouTube hesabının Google e-postasını ekle
     (uygulama Google tarafından doğrulanmadığı için sadece test kullanıcıları
     giriş yapabilir — bu senin kendi kullanımın için sorun değil).
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URI: `http://localhost:5000/auth/youtube/callback`
     (kendi domain'inde çalıştırırsan buraya onu yaz)
5. Oluşan client ID için "Download JSON" de, dosyayı proje kök dizinine
   `client_secret.json` adıyla koy.

### 5. Çalıştır

```bash
python app.py
```

Tarayıcıda `http://localhost:5000` aç. Önce sağ üstten **"YouTube hesabını bağla"**
diyerek bir kere yetkilendirme yap (Google giriş ekranı açılacak), sonra video
yükleyip klipleri üretebilirsin.

## Önemli notlar

- **YouTube günlük kota**: Google'ın varsayılan API kotası günlük 10.000 birim;
  bir video yükleme ~1600 birim tutar, yani günde ~6 video yükleyebilirsin.
  Daha fazlası için Google Cloud Console'dan kota artışı talep etmen gerekir.
- **Whisper modeli**: `.env` içindeki `WHISPER_MODEL` ayarı doğruluk/hız
  dengesini belirler (`tiny` en hızlı, `large-v3` en doğru ama yavaş/ağır).
  Sunucunda GPU yoksa `small` iyi bir başlangıç.
- **Depolama**: Yüklenen videolar `uploads/`, çıkan klipler `clips/` klasöründe
  tutulur — bunları düzenli temizlemen gerekebilir (disk dolabilir).
- **Üretim/production için**: Bu MVP dosya tabanlı job takibi ve thread ile arka
  plan işleme kullanıyor — tek sunucuda küçük/orta kullanım için yeterli.
  Çok kullanıcılı/yoğun kullanım için Redis + Celery gibi bir kuyruk sistemine
  geçmek daha sağlıklı olur.
- **Telif hakkı**: Yüklediğin videoların telif hakkına sen sahip olmalısın —
  başkasının içeriğini izinsiz kesip yayınlamak YouTube'un kurallarına ve
  telif hakkı yasalarına aykırı olabilir.

## Dosya yapısı

```
viral-clipper/
├── app.py              # Flask backend + pipeline
├── templates/index.html
├── static/{style.css,app.js}
├── requirements.txt
├── .env.example
├── client_secret.json  # sen ekleyeceksin (Google Cloud'dan)
├── uploads/             # yüklenen ham videolar
├── clips/               # üretilen klipler
└── jobs/                 # her işin durumu (JSON)
```
