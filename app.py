import os
import time
import random
from datetime import datetime

import streamlit as st
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# =========================================
# KONFIGURASI
# =========================================
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRET_FILE = "client_secret.json"   # file OAuth yang kamu download dari Google Cloud
TOKEN_FILE = "token_youtube.json"           # file untuk menyimpan access/refresh token
COMMENTS_FILE = "teks.txt"                  # file komentar per baris
LOG_FILE = "yt_comment.log"                 # file log


# =========================================
# FUNGSI AUTENTIKASI
# =========================================
def get_youtube_service():
    creds = None

    # Jika sudah pernah login, pakai token yang tersimpan
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Jika belum ada kredensial / sudah expired
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh token
            creds.refresh(Request())
        else:
            # Flow OAuth baru
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES
            )
            # Akan membuka browser untuk login Google
            creds = flow.run_local_server(port=0)

        # Simpan token ke file
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    youtube = build("youtube", "v3", credentials=creds)
    return youtube


# =========================================
# BACA KOMENTAR DARI teks.txt
# =========================================
def load_random_comments(file_path=COMMENTS_FILE):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File {file_path} tidak ditemukan!")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]

    if not lines:
        raise ValueError(f"File {file_path} kosong atau tidak berisi teks valid.")

    return lines


# =========================================
# UTIL: Ambil Uploads Playlist ID dari Channel
# =========================================
def get_uploads_playlist_id(youtube, channel_id: str):
    """
    Ambil playlistId untuk daftar upload (uploads) dari suatu channel.
    """
    request = youtube.channels().list(
        part="contentDetails",
        id=channel_id
    )
    response = request.execute()

    items = response.get("items", [])
    if not items:
        raise ValueError("Channel ID tidak ditemukan atau tidak memiliki contentDetails.")

    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return uploads_playlist_id


# =========================================
# UTIL: Ambil daftar video dari uploads playlist
# =========================================
def get_videos_from_uploads(youtube, uploads_playlist_id: str, max_videos: int = 10):
    """
    Ambil daftar videoId dari playlist uploads, maksimal max_videos.
    (Default order: upload terbaru dulu).
    """
    video_ids = []
    next_page_token = None

    while len(video_ids) < max_videos:
        request = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=min(50, max_videos - len(video_ids)),
            pageToken=next_page_token
        )
        response = request.execute()

        for item in response.get("items", []):
            video_id = item["contentDetails"]["videoId"]
            video_ids.append(video_id)
            if len(video_ids) >= max_videos:
                break

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids


# =========================================
# UTIL: Ambil channel ID akun yang login
# =========================================
def get_my_channel_id(youtube):
    resp = youtube.channels().list(
        part="id",
        mine=True
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError("Tidak bisa mengambil channel ID dari akun yang login.")
    return items[0]["id"]


# =========================================
# UTIL: Cek apakah akun ini sudah pernah komentar di video tsb
# =========================================
def has_commented_on_video(youtube, video_id: str, my_channel_id: str):
    """
    True kalau channel my_channel_id sudah pernah memberikan top-level comment
    di video dengan video_id.
    """
    page_token = None

    while True:
        request = youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=100,
            textFormat="plainText",
            pageToken=page_token
        )
        response = request.execute()

        for item in response.get("items", []):
            top_snippet = item["snippet"]["topLevelComment"]["snippet"]
            author_channel = top_snippet.get("authorChannelId", {}).get("value")
            if author_channel == my_channel_id:
                return True

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return False


# =========================================
# UTIL: Post comment ke satu video
# =========================================
def post_comment(youtube, video_id: str, text: str):
    body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {
                "snippet": {
                    "textOriginal": text
                }
            }
        }
    }

    request = youtube.commentThreads().insert(
        part="snippet",
        body=body
    )
    response = request.execute()
    return response


# =========================================
# UTIL: Like satu video
# =========================================
def like_video(youtube, video_id: str):
    """
    Kasih LIKE ke video dengan videos.rate
    rating bisa: like, dislike, none
    Di sini kita pakai 'like'.
    """
    request = youtube.videos().rate(
        id=video_id,
        rating="like"
    )
    request.execute()  # tidak ada response body, kalau sukses tidak error


# =========================================
# STREAMLIT APP
# =========================================
def main():
    st.set_page_config(page_title="YouTube Auto Comment (Channel Sendiri)", layout="centered")

    st.title("📝 YouTube Auto Comment + Auto Like (Channel Sendiri)")
    st.markdown(
        """
        Tools ini dibuat untuk **otomatisasi komentar + like pada video di channel milik sendiri**
        dengan menggunakan **YouTube Data API v3**.

        ⚠️ **PENTING (Anti-Spam & Legal):**
        - Hanya gunakan untuk **channel YouTube milik sendiri**.
        - Jangan dipakai untuk **spam di channel orang lain**.
        - Patuh pada **YouTube Terms of Service** dan kebijakan **anti-spam**.
        - Terlalu agresif (banyak video, jeda terlalu cepat, komentar sama persis berulang) bisa menyebabkan **suspend / ban**.
        """
    )

    st.divider()

    # Konfirmasi anti-spam
    confirm = st.checkbox(
        "Saya mengerti dan setuju untuk hanya menggunakan tools ini pada channel milik saya sendiri dan tidak untuk spam.",
        value=False
    )

    st.subheader("1️⃣ Autentikasi YouTube API")

    if not os.path.exists(CLIENT_SECRET_FILE):
        st.error(f"File `{CLIENT_SECRET_FILE}` tidak ditemukan. Silakan taruh file OAuth client secret di folder yang sama dengan app.")
        st.stop()

    auth_btn = st.button("🔐 Login / Refresh YouTube OAuth")

    if auth_btn:
        try:
            youtube = get_youtube_service()
            st.success("Berhasil autentikasi ke YouTube API.")
        except Exception as e:
            st.error(f"Gagal autentikasi: {e}")
            st.stop()

    # Cek apakah token sudah ada
    youtube = None
    if os.path.exists(TOKEN_FILE):
        try:
            youtube = get_youtube_service()
            st.success("Kredensial YouTube sudah siap digunakan.")
        except Exception as e:
            st.warning(f"Token ada tapi gagal digunakan: {e}")

    st.divider()

    st.subheader("2️⃣ Pengaturan Channel & Batasan")

    channel_id = st.text_input(
        "Channel ID target (contoh: UCxxxxxx)",
        help="Gunakan Channel ID dari channel yang akan dikomentari (sebaiknya channel milik sendiri)."
    )

    max_videos = st.number_input(
        "Maksimal jumlah video yang akan dikomentari",
        min_value=1,
        max_value=500,
        value=10,
        step=1,
        help="Video akan diambil dari playlist uploads channel, default-nya dari upload terbaru."
    )

    order_option = st.radio(
        "Urutan video yang diproses",
        ["Terbaru ke terlama", "Terlama ke terbaru"],
        index=0,
        help="Pilih apakah mau mulai dari video terbaru dulu atau dari video terlama dulu."
    )

    delay_seconds = st.number_input(
        "Jeda (detik) antar komentar/like",
        min_value=10,
        max_value=3600,
        value=60,
        step=5,
        help="Disarankan jeda yang cukup panjang agar tidak terlihat seperti spam."
    )

    like_option = st.checkbox(
        "👍 Like juga semua video yang dikomentari?",
        value=True,
        help="Jika aktif, setiap video yang dikomentari akan di-LIKE dengan akun yang sama."
    )

    skip_if_commented = st.checkbox(
        "⏭ Skip video yang sudah pernah dikomentari oleh akun ini",
        value=True,
        help="Cek komentar existing dan lewati video yang sudah pernah dikomentari."
    )

    check_existing_limit = 0
    if skip_if_commented:
        check_existing_limit = st.number_input(
            "Cek komentar existing hanya untuk N video pertama (hemat kuota)",
            min_value=1,
            max_value=500,
            value=10,
            step=1,
            help="Untuk hemat kuota: hanya N video pertama yang dicek sudah pernah dikomentari atau belum."
        )

    st.divider()

    st.subheader("3️⃣ Sumber Komentar (teks.txt)")

    st.caption(
        f"""
        Komentar akan dibaca dari file **`{COMMENTS_FILE}`** di folder yang sama dengan `app.py`.
        - Setiap **baris = 1 komentar**
        - Komentar akan di-**acak sekali**, lalu dipakai **berurutan tanpa duplikasi**
        - Jika komentar habis, proses akan berhenti.
        """
    )

    random_comments = []
    try:
        random_comments = load_random_comments(COMMENTS_FILE)
        st.success(f"Ditemukan {len(random_comments)} komentar di `{COMMENTS_FILE}`.")
        st.code("\n".join(random_comments[:5]) + ("\n..." if len(random_comments) > 5 else ""), language="text")
    except Exception as e:
        st.error(f"Komentar belum siap: {e}")

    st.divider()

    start_btn = st.button("🚀 Mulai Proses Komentar (dan Like)")

    if start_btn:
        if not confirm:
            st.error("Kamu harus mencentang checkbox persetujuan anti-spam terlebih dahulu.")
            return

        if youtube is None:
            st.error("YouTube belum terautentikasi. Klik dulu tombol 'Login / Refresh YouTube OAuth'.")
            return

        if not channel_id.strip():
            st.error("Channel ID tidak boleh kosong.")
            return

        if not random_comments:
            st.error("Komentar dari teks.txt belum tersedia / bermasalah.")
            return

        try:
            # Ambil channel ID akun yang login
            my_channel_id = get_my_channel_id(youtube)
            st.info(f"Channel ID akun yang login: {my_channel_id}")

            st.info("Mengambil uploads playlist dari channel target...")
            uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id.strip())
            st.success(f"Berhasil mendapatkan uploads playlist ID: {uploads_playlist_id}")

            st.info(f"Mengambil daftar video (maks {int(max_videos)})...")
            video_ids = get_videos_from_uploads(
                youtube,
                uploads_playlist_id,
                max_videos=int(max_videos)
            )

            if not video_ids:
                st.warning("Tidak ada video yang ditemukan di uploads playlist.")
                return

            # Atur urutan video sesuai pilihan user
            if order_option == "Terlama ke terbaru":
                video_ids = list(reversed(video_ids))

            total = len(video_ids)
            st.success(f"Ditemukan {total} video untuk diproses (urutan: {order_option}).")

            # Acak komentar sekali, lalu pakai berurutan
            shuffled_comments = random_comments.copy()
            random.shuffle(shuffled_comments)
            comment_index = 0  # pointer komentar yang dipakai

            progress_bar = st.progress(0)
            status_text = st.empty()
            log_box = st.empty()
            logs = []

            # === Siapkan file log untuk sesi ini ===
            session_header = f"\n===== RUN {datetime.now().isoformat()} | order={order_option} =====\n"
            with open(LOG_FILE, "a", encoding="utf-8") as log_file:
                log_file.write(session_header)

                processed_videos = 0

                for idx, vid in enumerate(video_ids, start=1):
                    status_text.markdown(
                        f"⏳ Memproses video `{vid}` ({idx}/{total})..."
                    )

                    # Skip pengecekan jika user tidak mengaktifkan fitur ini
                    # atau index video di luar batas cek (hemat kuota)
                    if skip_if_commented and idx <= int(check_existing_limit):
                        try:
                            if has_commented_on_video(youtube, vid, my_channel_id):
                                line = f"⏭ [{idx}/{total}] Skip → VideoId={vid} (sudah pernah dikomentari oleh channel ini)"
                                logs.append(line)
                                log_file.write(line + "\n")
                                log_box.code("\n".join(logs), language="text")
                                progress_bar.progress(idx / total)
                                continue
                        except Exception as e:
                            line = f"⚠️ [{idx}/{total}] Gagal cek komentar existing → VideoId={vid} | ERROR: {e}"
                            logs.append(line)
                            log_file.write(line + "\n")
                    elif skip_if_commented and idx > int(check_existing_limit):
                        # Tidak cek existing untuk video di luar limit
                        line = f"ℹ️ [{idx}/{total}] Tidak cek komentar existing (di luar N={check_existing_limit} video pertama) → VideoId={vid}"
                        logs.append(line)
                        log_file.write(line + "\n")

                    # Cek apakah masih ada komentar tersisa
                    if comment_index >= len(shuffled_comments):
                        line = "⛔ Komentar di teks.txt sudah habis. Proses dihentikan."
                        logs.append(line)
                        log_file.write(line + "\n")
                        log_box.code("\n".join(logs), language="text")
                        break

                    chosen_comment = shuffled_comments[comment_index]
                    comment_index += 1
                    processed_videos += 1

                    # Kirim komentar
                    try:
                        post_comment(youtube, vid, chosen_comment)
                        line = f"✅ Komentar → VideoId={vid} | \"{chosen_comment}\""
                    except Exception as e:
                        line = f"❌ Gagal komentar → VideoId={vid} | ERROR: {e}"
                    logs.append(line)
                    log_file.write(line + "\n")

                    # Like jika diaktifkan
                    if like_option:
                        try:
                            like_video(youtube, vid)
                            line = f"👍 Like → VideoId={vid}"
                        except Exception as e:
                            line = f"⚠️ Gagal like → VideoId={vid} | ERROR: {e}"
                        logs.append(line)
                        log_file.write(line + "\n")

                    # Update log di UI
                    log_box.code("\n".join(logs), language="text")

                    # Update progress bar
                    progress_bar.progress(idx / total)

                    # Jeda antar aksi (tidak perlu sleep setelah yang terakhir / kalau komentar habis)
                    if idx < total and comment_index < len(shuffled_comments):
                        time.sleep(delay_seconds)

                footer = f"===== END (video dikomentari: {processed_videos}) =====\n"
                log_file.write(footer)

            status_text.markdown("🎉 Selesai memproses video (atau komentar habis).")
            st.success(
                f"Proses selesai. Total video yang dikomentari: {processed_videos}. "
                f"Log detail tersimpan di file `{LOG_FILE}`. "
                "Cek YouTube Studio untuk memastikan komentar & like masuk dan tidak melanggar kebijakan."
            )

        except Exception as e:
            st.error(f"Terjadi error saat proses: {e}")


if __name__ == "__main__":
    main()
