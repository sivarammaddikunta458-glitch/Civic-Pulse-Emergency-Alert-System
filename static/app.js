const $ = (id) => document.getElementById(id);
let currentUserId = Number(localStorage.getItem("civicPulseUserId")) || null;
const PENDING_ALERTS_KEY = "civicPulsePendingAlerts";

function showMessage(text, error = false) {
  const el = $("citizenMessage"); el.textContent = text; el.style.color = error ? "#c33d35" : "#126b52";
}

async function apiRequest(url, options) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function pendingAlerts() {
  const saved = JSON.parse(localStorage.getItem(PENDING_ALERTS_KEY) || "[]");
  if (!Array.isArray(saved)) throw new Error("Stored offline alerts are in an invalid format.");
  return saved;
}

function savePendingAlerts(alerts) {
  localStorage.setItem(PENDING_ALERTS_KEY, JSON.stringify(alerts));
}

function newRequestId() {
  return crypto.randomUUID();
}

function updateNetwork() {
  const badge = $("networkBadge");
  badge.textContent = navigator.onLine ? "● Online" : "● Offline — alerts queued on this device";
  badge.style.color = navigator.onLine ? "#9be8c5" : "#ffd89b";
}

async function registerUser(event) {
  event.preventDefault();
  const payload = {name: $("name").value, phone: $("phone").value, emergency_contact_name: $("contactName").value, emergency_contact_phone: $("contactPhone").value};
  try {
    const data = await apiRequest("/api/users", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    currentUserId = data.id; localStorage.setItem("civicPulseUserId", currentUserId); showMessage("Your details are saved and ready for an alert.");
  } catch (error) {
    showMessage(error.message, true);
  }
}

function getLocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve({});
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({latitude:position.coords.latitude, longitude:position.coords.longitude}),
      () => resolve({}),
      {enableHighAccuracy:true, timeout:7000}
    );
  });
}

async function createAlert() {
  if (!currentUserId) return showMessage("Save your name and phone number first.", true);
  const location = await getLocation();
  const payload = {request_id:newRequestId(), user_id:currentUserId, category:$("category").value, ...location};
  if (!navigator.onLine) return queueAlert(payload);
  try {
    const data = await apiRequest("/api/alerts", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    showMessage(data.sms_sent ? "Alert sent to the response team." : "Alert saved on the server. SMS was not sent.");
    await refreshAlerts();
  } catch (error) {
    if (error.status) showMessage(error.message, true);
    else queueAlert(payload, error.message);
  }
}

function statusClass(status) { return status === "Alert Sent" ? "sent" : (status === "Resolved" ? "resolved" : ""); }
function renderAlerts(alerts) {
  const counts = alerts.reduce((all, item) => { all[item.status] = (all[item.status] || 0) + 1; return all; }, {});
  const summary = $("summary");
  summary.replaceChildren();
  [`${alerts.length} total`, ...Object.entries(counts).map(([key, value]) => `${key}: ${value}`)].forEach((text) => {
    const item = document.createElement("span"); item.textContent = text; summary.append(item);
  });
  const container = $("alerts");
  container.replaceChildren();
  if (!alerts.length) {
    const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No alerts yet. New emergency reports will appear here."; container.append(empty); return;
  }
  alerts.forEach((alert) => {
    const article = document.createElement("article"); article.className = "alert";
    const head = document.createElement("div"); head.className = "alert-head";
    const details = document.createElement("div");
    const title = document.createElement("h3"); title.textContent = `${alert.category} — ${alert.name}`;
    const date = document.createElement("p"); date.textContent = `${new Date(alert.created_at).toLocaleString()} · ${alert.phone}`;
    details.append(title, date);
    const status = document.createElement("span"); status.className = `status ${statusClass(alert.status)}`; status.textContent = alert.status;
    head.append(details, status);
    const message = document.createElement("p"); message.textContent = alert.message;
    article.append(head, message);
    if (alert.map_url) {
      const link = document.createElement("a"); link.href = alert.map_url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "View location ↗"; article.append(link);
    } else {
      const unavailable = document.createElement("p"); unavailable.textContent = "Location unavailable"; article.append(unavailable);
    }
    const select = document.createElement("select"); select.dataset.alertId = alert.id; select.setAttribute("aria-label", "Update alert status");
    window.STATUSES.forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; option.selected = value === alert.status; select.append(option); });
    select.addEventListener("change", async (event) => {
      try {
        await apiRequest(`/api/alerts/${event.target.dataset.alertId}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({status:event.target.value})});
        await refreshAlerts();
      } catch (error) { showMessage(error.message, true); }
    });
    article.append(select); container.append(article);
  });
}
async function refreshAlerts() {
  try { renderAlerts(await apiRequest("/api/alerts")); }
  catch (error) { $("alerts").textContent = error.message; }
}
function queueAlert(payload, reason) {
  try {
    const queued = pendingAlerts(); queued.push(payload); savePendingAlerts(queued);
    showMessage(reason ? `Alert queued on this device because the server is unavailable (${reason}).` : "Alert queued on this device. It will sync when the server is reachable.");
  } catch (error) {
    showMessage(`Could not save this alert on the device: ${error.message}`, true);
  }
}
async function retryAlerts() {
  let queued;
  try { queued = pendingAlerts(); }
  catch (error) { showMessage(error.message, true); return; }
  let synced = 0;
  while (queued.length) {
    try {
      await apiRequest("/api/alerts", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(queued[0])});
      queued.shift(); synced++;
      savePendingAlerts(queued);
    } catch (error) {
      showMessage(`Synced ${synced}; ${queued.length} alert(s) remain on this device. ${error.message}`, true);
      return;
    }
  }
  try {
    const data = await apiRequest("/api/alerts/retry", {method:"POST"});
    showMessage(`Synced ${synced} device alert(s); checked ${data.checked} server alert(s), ${data.sent} sent.`);
    await refreshAlerts();
  } catch (error) { showMessage(error.message, true); }
}

$("registrationForm").addEventListener("submit", registerUser); $("alertButton").addEventListener("click", createAlert); $("retryButton").addEventListener("click", retryAlerts); $("refreshButton").addEventListener("click", refreshAlerts);
window.addEventListener("online", () => { updateNetwork(); retryAlerts(); }); window.addEventListener("offline", updateNetwork); updateNetwork(); refreshAlerts(); retryAlerts();
