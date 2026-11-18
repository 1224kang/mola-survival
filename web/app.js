"use strict";

const byId = (id) => document.getElementById(id);
const sprites = {
  idle: ["짝짝이 눈으로 웃는 개복치", "./assets/mola-idle.png"],
  hungry: ["새우를 기다리며 침을 흘리는 개복치", "./assets/mola-hungry.png"],
  dead: ["눈을 감은 개복치", "./assets/mola-dead.png"],
};
const causes = { hunger: "굶주림", starvation: "굶주림", stress: "스트레스", overfeeding: "과식" };
const causeText = (cause) => causes[cause] || cause || "—";
const seconds = (ms) => (Math.max(0, ms) / 1000).toFixed(1);
const milliseconds = (ms) => Number(ms).toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + " ms";
let busy = false;
let generation = 0;
let view = "tank";
let currentState = {};
let renderedRows = "";
let renderedQueries = "";

function renderHistory(history) {
  const key = JSON.stringify(history?.rows || []);
  if (key !== renderedRows) {
    renderedRows = key;
    const rows = document.createDocumentFragment();
    for (const record of history?.rows || []) {
      const row = document.createElement("div");
      row.className = "history-entry";
      const name = document.createElement("strong");
      name.className = "history-name";
      const fish = new Image(35, 35);
      fish.src = sprites.dead[1];
      fish.alt = "";
      name.append(fish, document.createTextNode(record.name));
      const duration = document.createElement("span");
      duration.textContent = seconds(record.survival_ms) + "초";
      const reason = document.createElement("span");
      reason.className = "reason";
      reason.textContent = causeText(record.cause);
      row.append(name, duration, reason);
      rows.append(row);
    }
    byId("history-rows").replaceChildren(rows);
  }
  byId("empty-history").hidden = Boolean(history?.rows?.length);
  byId("empty-history").textContent = history ? "아직 떠나보낸 복치가 없어요." : "기록을 불러오는 중이에요.";
}

function render(state) {
  currentState = state;
  const { pet, history, benchmark, queries = [] } = state;
  const alive = pet?.status === "alive";
  const dead = pet?.status === "dead";
  const stage = dead ? "dead" : alive && pet.remaining_ms <= 3000 ? "hungry" : "idle";
  byId("tank").dataset.stage = pet ? stage : "ready";
  const [alt, src] = sprites[stage];
  if (byId("mascot").getAttribute("src") !== src) byId("mascot").src = src;
  byId("mascot").alt = alt;
  byId("status-text").textContent = dead ? "부고" : alive ? stage === "hungry" ? "배고픔" : "생존 중" : "입양 대기";
  byId("game-title").textContent = dead ? pet.name + "의 부고" : alive ? pet.name : "복치에게 이름을 지어주세요";
  byId("game-message").textContent = dead
    ? "생존 " + seconds(pet.survival_ms) + "초 · 사인 " + causeText(pet.cause)
    : alive ? (stage === "hungry" ? "배고파요. 지금 새우가 필요해요!" : "새우를 주면 4초가 늘어요. 15초를 넘기면 과식해요.")
    : "입양하면 10초의 생존 타이머가 시작돼요.";
  byId("adopt-form").hidden = Boolean(alive);
  byId("adopt").textContent = dead ? "다시 입양하기" : "입양하기";
  byId("feed").hidden = !alive;
  byId("ttl").textContent = pet ? seconds(alive ? pet.remaining_ms : 0) : "—";
  byId("ttl-bar").style.width = (alive ? Math.min(100, Math.max(0, pet.remaining_ms / 150)) : 0) + "%";
  byId("stress").textContent = (pet?.stress || 0) + "/3";
  document.querySelectorAll(".dots i").forEach((dot, index) => dot.classList.toggle("lit", index < (pet?.stress || 0)));
  byId("key-state").textContent = alive ? "유효" : dead ? (causeText(pet.cause) === "굶주림" ? "TTL 만료" : "삭제됨") : "입양 대기";
  byId("cause-row").hidden = !dead;
  byId("cause").textContent = causeText(pet?.cause);
  const queryKey = JSON.stringify(queries);
  if (queryKey !== renderedQueries) {
    renderedQueries = queryKey;
    byId("query-empty").hidden = queries.length > 0;
    byId("query-log").replaceChildren(...queries.map((query) => {
      const item = document.createElement("li");
      const sql = document.createElement("code");
      sql.textContent = query.sql;
      const detail = document.createElement("small");
      detail.textContent = (query.ok ? "성공" : "실패")
        + (query.inserted_id != null ? " · ID " + query.inserted_id : "")
        + (query.count > 1 ? " · 연속 " + query.count + "회" : "");
      item.append(sql, detail);
      return item;
    }));
  }
  byId("cache-hit").textContent = history ? history.hit ? "HIT" : "MISS" : "조회 대기";
  byId("cache-hit").className = "chip" + (history ? history.hit ? " hit" : " miss" : "");
  byId("lookup-time").textContent = history ? milliseconds(history.ms) : "—";
  byId("lookup-db").textContent = history ? history.db_calls + "회" : "—";
  for (const mode of ["off", "on"]) {
    byId(mode + "-time").textContent = benchmark ? milliseconds(benchmark[mode].ms) : "—";
    byId(mode + "-db").textContent = benchmark ? benchmark[mode].db_calls + "회" : "—";
  }
  renderHistory(history);
}

async function request(endpoint, body) {
  const options = { cache: "no-store" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/api/" + endpoint, options);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || "요청을 처리하지 못했어요.");
  return data;
}

function showError(message) {
  byId("error").textContent = message || "";
  byId("error").hidden = !message;
}

let connectionError = false;
async function poll() {
  const started = generation;
  if (!busy) {
    try {
      const data = await request("state");
      if (started === generation) {
        render(data);
        if (connectionError) showError("");
        connectionError = false;
      }
    } catch (error) {
      if (started === generation) {
        connectionError = true;
        showError(error.message);
      }
    }
  }
  window.setTimeout(poll, 300);
}

async function act(endpoint, body = {}) {
  if (busy) return;
  busy = true;
  generation++;
  showError("");
  for (const id of ["adopt", "feed", "benchmark", "reload-history", "history-tab"]) byId(id).disabled = true;
  if (endpoint === "benchmark") byId("benchmark").textContent = "측정 중…";
  try {
    const result = await request(endpoint, body);
    render(result);
    if (endpoint === "adopt") byId("pet-name").value = "";
  } catch (error) {
    showError(error.message);
  } finally {
    for (const id of ["adopt", "feed", "benchmark", "reload-history", "history-tab"]) byId(id).disabled = false;
    byId("benchmark").innerHTML = '복치 기록 100번 열기 <span aria-hidden="true">↗</span>';
    busy = false;
  }
}

function selectView(next) {
  view = next;
  const history = view === "history";
  byId("tank-view").hidden = history;
  byId("history-view").hidden = !history;
  byId("tank-tab").setAttribute("aria-pressed", String(!history));
  byId("history-tab").setAttribute("aria-pressed", String(history));
  if (history) act("history");
}

byId("adopt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = byId("pet-name").value.trim();
  if (!name) {
    byId("pet-name").focus();
    showError("복치 이름을 입력해주세요.");
    return;
  }
  act("adopt", { name });
});
byId("feed").addEventListener("click", () => act("feed", { id: currentState.pet?.id }));
byId("tank-tab").addEventListener("click", () => selectView("tank"));
document.querySelector(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  selectView("tank");
});
byId("history-tab").addEventListener("click", () => selectView("history"));
byId("reload-history").addEventListener("click", () => act("history"));
byId("benchmark").addEventListener("click", () => act("benchmark"));
render(currentState);
poll();
