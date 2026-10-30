# DUBBER AI PRO — Android App + Server (Phase 1)

នេះជាកំណែទូរស័ព្ទរបស់ `main-2.py` (DUBBER AI PRO v1.2.9) តែ **មិនមែន copy កូដ PyQt ទៅទូរស័ព្ទទេ**។
PyQt6 ដំណើរការលើកុំព្យូទ័រតែប៉ុណ្ណោះ ដូច្នេះគម្រោងនេះបែងចែកជា ២ ផ្នែក៖

```
ទូរស័ព្ទ Android (Kotlin + Compose)  ──►  Server (FastAPI + Python)
  • ជ្រើសវីដេអូ, កែ segment          • Gemini transcribe
  • បកប្រែ, បង្កើតសំឡេង, export       • Gemini/Google translate
  • ទាញយកវីដេអូដែល dub រួច            • Edge TTS + ffmpeg mix/export
```

## Phase 1 មានអ្វីខ្លះ (ធ្វើរួច)

- Upload វីដេអូពីទូរស័ព្ទ → server ថតសំឡេងដោយ Gemini (fallback chain ដូច desktop៖
  model ដែលជ្រើស → gemini-3.6-flash → gemini-3.5-flash → gemini-2.5-flash)
- កែតារាង segment នៅលើទូរស័ព្ទ (អត្ថបទបកប្រែ, ពេលចាប់ផ្តើម/បញ្ចប់)
  ហើយ server ផ្ទៀងផ្ទាត់ថាមិនមាន segment ត្រួតគ្នាទេ មុនពេល export
- បកប្រែ (Google ជាលំនាំដើម, Gemini ជាជម្រើស)
- បង្កើតសំឡេង Edge TTS តាម segment (voice/pitch/rate ដូច desktop)
- Export វីដេអូ dub៖ សំឡេងដើមបន្ថយនៅ 30% + សំឡេង TTS លាយតាមពេលរបស់ segment,
  វីដេអូ copy ត្រង់ៗ មិន re-encode
- ទាញយក dubbed.mp4 និង dubbed.srt

## Phase 2 នៅមិនទាន់មាន (ប្រាប់ត្រង់ៗ)

Server មាន endpoint `/jobs/{id}/phase2/{feature}` ដែលឆ្លើយ 501 ដោយចេតនា
(មិនមែនធ្វើពុតថាជោគជ័យទេ) សម្រាប់៖ blur, text/logo overlay, burn subtitle,
Demucs បំបែក vocal, lip-sync, batch។ មុខងារទាំងនេះត្រូវបន្ថែមម្តងមួយៗ
នៅលើ server តែប៉ុណ្ណោះ — app ទូរស័ព្ទមិនចាំបាច់សរសេរឡើងវិញទេ។

Demucs និង lip-sync ត្រូវការ server ធំ (GPU) ដាច់ដោយឡែក មិនមែន server
ធម្មតាសម្រាប់ Phase 1 ទេ។

## ដំណើរការ Server ដោយគ្មាន laptop

ជម្រើសងាយបំផុត៖ ដាក់កូដនេះលើ GitHub (ធ្វើពី browser ទូរស័ព្ទបាន) រួច deploy
នៅ Render / Railway / VPS ដែលគាំទ្រ Dockerfile៖

1. Upload folder នេះទៅ GitHub repo ថ្មី (folder `server/` ជា root សម្រាប់ deploy
   ឬ deploy Dockerfile នៅ `server/Dockerfile`)។
2. កំណត់ Environment Variable៖ `GEMINI_API_KEY=...` (បើមិនកំណត់ទេ app
   ត្រូវបញ្ចូល key នៅលើទូរស័ព្ទ ហើយ key នោះប្រើតែសម្រាប់ job នោះទេ)។
3. យក URL របស់ server មកដាក់ក្នុង app។
4. សាកល្បង៖ បើក `https://your-server/health` ត្រូវឃើញ `{"ok": true}`។

បើ run លើម៉ាស៊ីនខ្លួនឯង៖

```bash
cd server
pip install -r requirements.txt
# ត្រូវមាន ffmpeg + ffprobe ក្នុង PATH
GEMINI_API_KEY=... uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Build APK ដោយគ្មាន laptop

1. Push កូដទៅ GitHub។
2. បើក GitHub លើ browser ទូរស័ព្ទ → tab Actions → workflow
   “Build Android APK” → Run workflow។
3. ទាញ APK ពី Artifacts → install លើទូរស័ព្ទខ្លួនឯងសម្រាប់សាកល្បង។

APK នេះជា debug APK — សម្រាប់ចែកចាយលើ Play Store ត្រូវបន្ថែម keystore
និង signed release build នៅពេលក្រោយ។

## API សង្ខេប

| Method | Endpoint | ធ្វើអ្វី |
|---|---|---|
| POST | /jobs/transcribe (multipart `file`) | Upload + transcribe |
| GET | /jobs/{id} | ស្ថានភាព + segments (app poll រៀងរាល់ 3 វិនាទី) |
| POST | /jobs/{id}/segments | រក្សាទុក segment ដែលកែនៅលើទូរស័ព្ទ |
| POST | /jobs/{id}/translate | បកប្រែ |
| POST | /jobs/{id}/tts | បង្កើតសំឡេង |
| POST | /jobs/{id}/export | Export វីដេអូ dub |
| GET | /jobs/{id}/download?type=video\|srt\|tts_zip | ទាញយកលទ្ធផល |

## សុវត្ថិភាព — អានមុន deploy

- **កុំ commit Gemini key ទៅ GitHub ឡើយ។** ដាក់តែក្នុង Environment Variable
  របស់ hosting ប៉ុណ្ណោះ។
- Phase 1 នេះគ្មាន login ទេ៖ អ្នកណាមាន URL server ក៏ប្រើបានដែរ។
  ដូច្នេះ deploy សម្រាប់ប្រើខ្លួនឯងសិន ឬដាក់នៅពីក្រោយ reverse proxy
  ដែលមាន basic auth មុននឹងចែក URL ឲ្យអ្នកដទៃ។
- នៅលើទូរស័ព្ទ key ត្រូវបានរក្សាក្នុង SharedPreferences ធម្មតា
  (Phase 1) — សម្រាប់កំណែចុងក្រោយគួរប្តូរទៅ EncryptedSharedPreferences។

## រចនាសម្ព័ន្ធ folder

```
server/app/main.py       FastAPI + jobs + Gemini/TTS/export pipeline
server/app/srt_utils.py  parse/format/validate SRT (segment model)
server/app/media.py      ffmpeg: extract audio, probe, mix dubbed video
server/Dockerfile        python + ffmpeg image
android/                 Kotlin + Compose app (Api.kt, MainActivity.kt)
.github/workflows/       build APK លើ GitHub Actions
```

## ដែនកំណត់ដែលត្រូវដឹង

- Job ត្រូវបានរក្សាក្នុង memory នៅលើ server៖ បើ server restart, job ចាស់ៗ
  បាត់ (file នៅលើ disk តែ status បាត់)។ កំណែចុងក្រោយត្រូវប្រើ database។
- វីដេអូវែង/ធំ ត្រូវការ server មាន RAM/disk គ្រប់គ្រាន់ និងពេល upload
  យូរ — នេះជាហេតុផលដែលការងារធ្ងន់មិនអាចធ្វើទាំងស្រុងលើទូរស័ព្ទបាន។
