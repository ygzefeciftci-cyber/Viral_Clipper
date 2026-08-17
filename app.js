const uploadBtn = document.getElementById("upload-btn");
const videoInput = document.getElementById("video-input");
const progressBox = document.getElementById("upload-progress");
const statusText = document.getElementById("status-text");
const logBox = document.getElementById("log-box");
const logList = document.getElementById("log-list");
const clipsBox = document.getElementById("clips-box");
const clipsGrid = document.getElementById("clips-grid");

const STATUS_LABELS = {
  queued: "Sırada...",
  transcribing: "Ses metne çevriliyor...",
  analyzing: "AI en iyi anları seçiyor...",
  cutting: "Klipler kesiliyor ve altyazı ekleniyor...",
  done: "Tamamlandı",
  error: "Hata oluştu",
};

uploadBtn.addEventListener("click", async () => {
  const file = videoInput.files[0];
  if (!file) {
    alert("Önce bir video seç.");
    return;
  }

  const formData = new FormData();
  formData.append("video", file);

  progressBox.classList.remove("hidden");
  logBox.classList.remove("hidden");
  uploadBtn.disabled = true;
  statusText.textContent = "Yükleniyor...";

  const res = await fetch("/api/upload", { method: "POST", body: formData });
  const data = await res.json();
  if (data.error) {
    statusText.textContent = "Hata: " + data.error;
    uploadBtn.disabled = false;
    return;
  }

  pollStatus(data.job_id);
});

function pollStatus(jobId) {
  const interval = setInterval(async () => {
    const res = await fetch(`/api/status/${jobId}`);
    const job = await res.json();

    statusText.textContent = STATUS_LABELS[job.status] || job.status;
    renderLog(job.log);

    if (job.status === "done" || job.status === "error") {
      clearInterval(interval);
      uploadBtn.disabled = false;
      if (job.status === "done") {
        renderClips(jobId, job.clips);
      }
    }
  }, 2000);
}

function renderLog(lines) {
  logList.innerHTML = "";
  for (const line of lines) {
    const li = document.createElement("li");
    li.textContent = line;
    logList.appendChild(li);
  }
  logList.scrollTop = logList.scrollHeight;
}

function renderClips(jobId, clips) {
  clipsBox.classList.remove("hidden");
  clipsGrid.innerHTML = "";

  clips.forEach((clip, i) => {
    const card = document.createElement("div");
    card.className = "clip-card";

    card.innerHTML = `
      <video controls src="/clips/${clip.file}"></video>
      <div class="clip-info">
        <h4>${escapeHtml(clip.title)}</h4>
        <p>${escapeHtml(clip.reason || clip.description || "")}</p>
        <button data-index="${i}" ${clip.published ? "disabled" : ""}>
          ${clip.published ? "YouTube'a yüklendi ✓" : "YouTube'a Yayınla"}
        </button>
      </div>
    `;

    const btn = card.querySelector("button");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Yükleniyor...";
      const res = await fetch("/api/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, clip_index: i }),
      });
      const data = await res.json();
      if (data.error) {
        alert(data.error);
        btn.disabled = false;
        btn.textContent = "YouTube'a Yayınla";
      } else {
        btn.textContent = "YouTube'a yüklendi ✓";
      }
    });

    clipsGrid.appendChild(card);
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
