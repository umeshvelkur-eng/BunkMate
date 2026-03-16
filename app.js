const MIN_ATTENDANCE_PERCENT = 75;
const THEME_STORAGE_KEY = "srm-dashboard-theme";
const DEFAULT_THEME = {
  accent: "#2d7dff",
  text: "#f5f7ff",
  panel: "#111827"
};
const LOGIN_TIMEOUT_MS = 60000;

const authShell = document.getElementById("authShell");
const appShell = document.getElementById("appShell");
const loginForm = document.getElementById("loginForm");
const loginButton = document.getElementById("loginButton");
const loginError = document.getElementById("loginError");
const loginHintDynamic = document.getElementById("loginHintDynamic");
const usernameInput = document.getElementById("usernameInput");
const usernameHint = document.getElementById("usernameHint");
const captchaImage = document.getElementById("captchaImage");
const captchaInput = document.getElementById("captchaInput");
const captchaBlock = document.getElementById("captchaBlock");
const refreshCaptchaButton = document.getElementById("refreshCaptchaButton");
const passwordInput = document.getElementById("passwordInput");
const togglePasswordButton = document.getElementById("togglePasswordButton");
const logoutButton = document.getElementById("logoutButton");
const menuButtons = [...document.querySelectorAll(".menu-item")];
const sections = [...document.querySelectorAll(".view-section")];
const attendanceCardTemplate = document.getElementById("attendanceCardTemplate");

const studentNameEl = document.getElementById("studentName");
const studentMetaEl = document.getElementById("studentMeta");
const lastSyncedEl = document.getElementById("lastSynced");
const timetableBodyEl = document.getElementById("timetableBody");
const selectedDayOrderBodyEl = document.getElementById("selectedDayOrderBody");
const attendanceCardsEl = document.getElementById("attendanceCards");
const marksBodyEl = document.getElementById("marksBody");
const courseCardsEl = document.getElementById("courseCards");
const plannerBodyEl = document.getElementById("plannerBody");
const cgpaBodyEl = document.getElementById("cgpaBody");
const cgpaValueEl = document.getElementById("cgpaValue");
const profileGridEl = document.getElementById("profileGrid");
const overallAttendanceValueEl = document.getElementById("overallAttendanceValue");
const overallAttendanceMetaEl = document.getElementById("overallAttendanceMeta");
const timetableHeadingEl = document.getElementById("timetableHeading");
const timetableSubheadingEl = document.getElementById("timetableSubheading");
const todayDayOrderLabelEl = document.getElementById("todayDayOrderLabel");
const dayOrderButtons = [...document.querySelectorAll(".day-order-button[data-day-order]")];
const showAllDayOrdersButton = document.getElementById("showAllDayOrdersButton");
const allDayOrdersModal = document.getElementById("allDayOrdersModal");
const modalBackdrop = document.getElementById("modalBackdrop");
const closeAllDayOrdersButton = document.getElementById("closeAllDayOrdersButton");
const allDayOrdersContentEl = document.getElementById("allDayOrdersContent");

const accentPicker = document.getElementById("accentPicker");
const textPicker = document.getElementById("textPicker");
const panelPicker = document.getElementById("panelPicker");
const resetThemeButton = document.getElementById("resetThemeButton");
const installButton = document.getElementById("installButton");

let dashboardPayload = null;
let authContext = null;
let installPromptEvent = null;

applyStoredTheme();
loginForm.addEventListener("submit", handleLogin);
logoutButton.addEventListener("click", handleLogout);
refreshCaptchaButton.addEventListener("click", loadAuthContext);
togglePasswordButton.addEventListener("click", togglePasswordVisibility);
menuButtons.forEach((button) => button.addEventListener("click", () => activateSection(button.dataset.target)));
accentPicker.addEventListener("input", updateThemeFromControls);
textPicker.addEventListener("input", updateThemeFromControls);
panelPicker.addEventListener("input", updateThemeFromControls);
resetThemeButton.addEventListener("click", resetTheme);
installButton.addEventListener("click", installApp);
dayOrderButtons.forEach((button) => button.addEventListener("click", () => setSelectedDayOrder(button.dataset.dayOrder)));
showAllDayOrdersButton.addEventListener("click", openAllDayOrdersModal);
closeAllDayOrdersButton.addEventListener("click", closeAllDayOrdersModal);
modalBackdrop.addEventListener("click", closeAllDayOrdersModal);
window.addEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
window.addEventListener("appinstalled", handleAppInstalled);

restoreSession();
registerServiceWorker();

async function restoreSession() {
  try {
    const response = await fetch("/api/session");
    if (!response.ok) {
      showLoginScreen();
      await loadAuthContext();
      return;
    }

    dashboardPayload = await response.json();
    showDashboard(true);
  } catch (error) {
    showLoginScreen();
    await loadAuthContext();
  }
}

async function handleLogin(event) {
  event.preventDefault();
  loginError.hidden = true;
  loginHintDynamic.hidden = true;
  loginButton.disabled = true;
  loginButton.textContent = "Checking...";

  const formData = new FormData(loginForm);
  const username = String(formData.get("username") || "").trim();
  const password = String(formData.get("password") || "");
  const captcha = String(formData.get("captcha") || "").trim();

  try {
    const response = await withLoginTimeout(
      fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username,
          password,
          captcha,
          preloginId: authContext?.preloginId || ""
        })
      }),
      LOGIN_TIMEOUT_MS
    );
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || "Login failed.");
    }

    dashboardPayload = payload;
    authShell.classList.add("auth-exit");
    window.setTimeout(() => showDashboard(false), 460);
  } catch (error) {
    if (error?.message === "LOGIN_TIMEOUT") {
      const restored = await tryRecoverTimedOutLogin();
      if (restored) {
        return;
      }
      showLoginError("Login request timed out. The portal is taking longer than expected to complete sign-in.");
    } else {
      showLoginError(error.message);
    }
    await loadAuthContext(true);
  } finally {
    loginButton.disabled = false;
    loginButton.textContent = "Login";
  }
}

function withLoginTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("LOGIN_TIMEOUT")), ms);
    promise
      .then((value) => {
        window.clearTimeout(timer);
        resolve(value);
      })
      .catch((error) => {
        window.clearTimeout(timer);
        reject(error);
      });
  });
}

async function tryRecoverTimedOutLogin() {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await delay(2000);
    try {
      const response = await fetch("/api/session");
      if (!response.ok) {
        continue;
      }
      dashboardPayload = await response.json();
      authShell.classList.add("auth-exit");
      window.setTimeout(() => showDashboard(false), 460);
      return true;
    } catch (error) {
      // Keep polling briefly in case the backend is finalizing the session.
    }
  }
  return false;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function handleLogout() {
  try {
    await fetch("/api/logout", { method: "POST" });
  } catch (error) {
    // Ignore transport errors and reset locally.
  }

  dashboardPayload = null;
  appShell.classList.add("is-hidden");
  authShell.classList.remove("is-hidden", "auth-exit");
  loginForm.reset();
  await loadAuthContext();
  activateSection("timetableSection");
}

function showLoginScreen() {
  appShell.classList.add("is-hidden");
  authShell.classList.remove("is-hidden", "auth-exit");
}

function handleBeforeInstallPrompt(event) {
  event.preventDefault();
  installPromptEvent = event;
  installButton.classList.remove("is-hidden");
}

async function installApp() {
  if (!installPromptEvent) {
    return;
  }

  installPromptEvent.prompt();
  try {
    await installPromptEvent.userChoice;
  } finally {
    installPromptEvent = null;
    installButton.classList.add("is-hidden");
  }
}

function handleAppInstalled() {
  installPromptEvent = null;
  installButton.classList.add("is-hidden");
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  try {
    await navigator.serviceWorker.register("/service-worker.js");
  } catch (error) {
    // Ignore service worker errors and keep the app usable.
  }
}

async function loadAuthContext(preserveFeedback = false) {
  if (!preserveFeedback) {
    loginError.hidden = true;
    loginHintDynamic.hidden = true;
  }
  refreshCaptchaButton.disabled = true;
  refreshCaptchaButton.textContent = "Loading...";
  try {
    const response = await fetch("/api/auth-context");
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load SRM captcha.");
    }
    authContext = payload;
    applyAuthContext(payload);
  } catch (error) {
    showLoginError(error.message);
  } finally {
    refreshCaptchaButton.disabled = false;
    refreshCaptchaButton.textContent = "Refresh Captcha";
  }
}

function applyAuthContext(payload) {
  usernameInput.placeholder = payload.usernamePlaceholder || "Username";
  usernameHint.textContent = payload.usernameHint || "";

  if (payload.captchaRequired && payload.captchaImage) {
    captchaBlock.classList.remove("is-hidden");
    captchaInput.classList.remove("is-hidden");
    captchaImage.src = payload.captchaImage;
    captchaInput.value = "";
    captchaInput.required = true;
  } else {
    captchaBlock.classList.add("is-hidden");
    captchaInput.classList.add("is-hidden");
    captchaImage.removeAttribute("src");
    captchaInput.value = "";
    captchaInput.required = false;
  }
}

function togglePasswordVisibility() {
  const isPassword = passwordInput.type === "password";
  passwordInput.type = isPassword ? "text" : "password";
  togglePasswordButton.textContent = isPassword ? "Hide" : "Show";
}

function showLoginError(message) {
  loginError.textContent = message || "Login failed.";
  loginError.hidden = false;
  loginHintDynamic.textContent = deriveLoginHint(message || "");
  loginHintDynamic.hidden = false;
}

function deriveLoginHint(message) {
  const normalized = message.toLowerCase();

  if (normalized.includes("captcha")) {
    return "What is likely wrong: the captcha you entered is incorrect or expired.";
  }

  if (normalized.includes("email")) {
    return "What is likely wrong: the email address is incorrect for the Academia portal.";
  }

  if (normalized.includes("netid")) {
    return "What is likely wrong: the NetID format is incorrect. Use only your SRM NetID, not registration number.";
  }

  if (normalized.includes("password")) {
    return "What is likely wrong: the password is incorrect.";
  }

  if (normalized.includes("username") || normalized.includes("user")) {
    return "What is likely wrong: the username or NetID is incorrect.";
  }

  if (normalized.includes("expired")) {
    return "What is likely wrong: the login session expired, so a fresh captcha is required.";
  }

  if (normalized.includes("timed out")) {
    return "What is likely wrong: the Academia sign-in flow is not advancing in the background. I need the backend trace to see which step is stuck.";
  }

  return "What may be wrong: NetID, password, captcha, or the SRM portal flow itself.";
}

function showDashboard(fromSessionRestore) {
  renderDashboard(dashboardPayload);
  if (!fromSessionRestore) {
    authShell.classList.add("is-hidden");
  }
  appShell.classList.remove("is-hidden");
}

function renderDashboard(payload) {
  const attendanceMap = new Map(
    payload.attendance.map((subject) => [subject.subjectCode, computeAttendanceMetrics(subject)])
  );
  const attendanceNameMap = new Map(
    payload.attendance.map((subject) => [subject.subjectName.toLowerCase(), computeAttendanceMetrics(subject)])
  );
  const courseNameMap = new Map(
    payload.courses.map((course) => [course.subjectCode, course.subjectName])
  );

  studentNameEl.textContent = payload.student.name;
  studentMetaEl.textContent = [
    payload.student.registrationNumber,
    payload.student.specialisation,
    payload.student.section
  ].join(" | ");
  lastSyncedEl.textContent = formatDateTime(payload.lastSynced);

  renderTimetableHeading(payload.currentDayOrder, payload.planner);
  renderTimetable(payload.timetable, attendanceMap, attendanceNameMap, payload.planner || []);
  renderSelectedDayOrder(payload.dayOrderTimetables || {}, attendanceMap, attendanceNameMap, payload.currentDayOrder);
  renderAllDayOrdersModal(payload.dayOrderTimetables || {}, attendanceMap, attendanceNameMap);
  renderAttendance(payload.attendance);
  renderMarks(payload.marks, courseNameMap);
  renderCourses(payload.courses);
  renderPlanner(payload.planner || []);
  renderCgpa(payload.cgpaCourses);
  renderProfile(payload.student);
  activateSection("timetableSection");
}

function renderTimetableHeading(currentDayOrder, planner) {
  if (currentDayOrder) {
    timetableHeadingEl.textContent = `Day order ${currentDayOrder} timetable and attendance`;
    todayDayOrderLabelEl.textContent = `Today's detected day order: Day ${currentDayOrder}`;
  } else {
    timetableHeadingEl.textContent = "Today's timetable and attendance status";
    todayDayOrderLabelEl.textContent = "Today's day order was not detected.";
  }

  const todayEntry = (planner || []).find((entry) => isToday(entry.date));
  if (todayEntry) {
    timetableSubheadingEl.textContent = `${todayEntry.date} • ${todayEntry.title}`;
  } else if (currentDayOrder) {
    timetableSubheadingEl.textContent = `Showing classes for ${currentDayOrder} from the academic planner.`;
  } else {
    timetableSubheadingEl.textContent = "Planner day order was not detected, so the course list is shown as fallback.";
  }
}

function renderTimetable(timetable, attendanceMap, attendanceNameMap, planner) {
  timetableBodyEl.innerHTML = "";
  const todayEntry = (planner || []).find((entry) => isToday(entry.date));
  const isHolidayOrNoClass = todayEntry && (
    todayEntry.dayOrder === "-" ||
    ["holiday", "weekend", "no class"].includes(String(todayEntry.kind || "").toLowerCase())
  );

  if (isHolidayOrNoClass) {
    timetableBodyEl.innerHTML = `
      <tr>
        <td colspan="6" data-label="Status" class="subtle-text">${todayEntry.title || "Holiday"}${todayEntry.kind ? ` • ${todayEntry.kind}` : ""}</td>
      </tr>
    `;
    return;
  }

  if (!timetable.length) {
    timetableBodyEl.innerHTML = `<tr><td colspan="6" data-label="Status" class="subtle-text">Timetable is not available yet for the detected day order.</td></tr>`;
    return;
  }

  timetable.forEach((entry, index) => {
    const metrics = attendanceMap.get(entry.subjectCode) || attendanceNameMap.get((entry.subjectName || "").toLowerCase());
    const timeLabel = entry.endTime ? `${entry.startTime} - ${entry.endTime}` : entry.startTime;
    const row = document.createElement("tr");
    row.className = "row-animate";
    row.style.animationDelay = `${index * 70}ms`;
    row.innerHTML = `
      <td data-label="Day">${entry.day}</td>
      <td data-label="Time">${timeLabel}</td>
      <td data-label="Subject">
        <div class="subject-cell">
          <strong>${entry.subjectName}</strong>
          <div class="subject-summary-line">
            <span class="skip-inline">Can skip: ${metrics?.classesCanSkip ?? 0}</span>
            <span class="need-inline">Need: ${metrics?.classesNeededForMinimum ?? 0}</span>
          </div>
        </div>
      </td>
      <td data-label="Room">${entry.room}</td>
      <td data-label="Faculty">${entry.faculty}</td>
      <td data-label="Attendance">${buildRingMarkup(metrics?.currentPercentage ?? 0)}</td>
    `;
    timetableBodyEl.appendChild(row);
  });
}

function renderSelectedDayOrder(dayOrderTimetables, attendanceMap, attendanceNameMap, currentDayOrder) {
  const defaultDayOrder = currentDayOrder && dayOrderTimetables[currentDayOrder]?.length ? currentDayOrder : "1";
  setSelectedDayOrder(defaultDayOrder, dayOrderTimetables, attendanceMap, attendanceNameMap);
}

function setSelectedDayOrder(dayOrder, sourceTimetables = dashboardPayload?.dayOrderTimetables || {}, attendanceMap = null, attendanceNameMap = null) {
  const effectiveAttendanceMap = attendanceMap || new Map(
    (dashboardPayload?.attendance || []).map((subject) => [subject.subjectCode, computeAttendanceMetrics(subject)])
  );
  const effectiveAttendanceNameMap = attendanceNameMap || new Map(
    (dashboardPayload?.attendance || []).map((subject) => [subject.subjectName.toLowerCase(), computeAttendanceMetrics(subject)])
  );

  dayOrderButtons.forEach((button) => button.classList.toggle("is-active", button.dataset.dayOrder === dayOrder));
  selectedDayOrderBodyEl.innerHTML = "";

  const rows = sourceTimetables[dayOrder] || [];
  if (!rows.length) {
    selectedDayOrderBodyEl.innerHTML = `<tr><td colspan="6" data-label="Status" class="subtle-text">No timetable rows found for Day ${dayOrder}.</td></tr>`;
    return;
  }

  rows.forEach((entry, index) => {
    const metrics = effectiveAttendanceMap.get(entry.subjectCode) || effectiveAttendanceNameMap.get((entry.subjectName || "").toLowerCase());
    const row = document.createElement("tr");
    row.className = "row-animate";
    row.style.animationDelay = `${index * 60}ms`;
    row.innerHTML = `
      <td data-label="Period">${entry.day}</td>
      <td data-label="Time">${entry.startTime} - ${entry.endTime}</td>
      <td data-label="Subject"><strong>${entry.subjectName}</strong></td>
      <td data-label="Room">${entry.room}</td>
      <td data-label="Faculty">${entry.faculty}</td>
      <td data-label="Attendance">${buildRingMarkup(metrics?.currentPercentage ?? 0)}</td>
    `;
    selectedDayOrderBodyEl.appendChild(row);
  });
}

function renderAllDayOrdersModal(dayOrderTimetables, attendanceMap, attendanceNameMap) {
  allDayOrdersContentEl.innerHTML = "";
  ["1", "2", "3", "4", "5"].forEach((dayOrder) => {
    const rows = dayOrderTimetables[dayOrder] || [];
    const block = document.createElement("section");
    block.className = "panel-card";
    const tableRows = rows.length
      ? rows.map((entry) => {
          const metrics = attendanceMap.get(entry.subjectCode) || attendanceNameMap.get((entry.subjectName || "").toLowerCase());
          return `
            <tr>
              <td data-label="Period">${entry.day}</td>
              <td data-label="Time">${entry.startTime} - ${entry.endTime}</td>
              <td data-label="Subject">${entry.subjectName}</td>
              <td data-label="Room">${entry.room}</td>
              <td data-label="Faculty">${entry.faculty}</td>
              <td data-label="Attendance">${buildRingMarkup(metrics?.currentPercentage ?? 0)}</td>
            </tr>
          `;
        }).join("")
      : `<tr><td colspan="6" data-label="Status" class="subtle-text">No timetable rows found for Day ${dayOrder}.</td></tr>`;

    block.innerHTML = `
      <div class="section-subhead">
        <h4>Day ${dayOrder}</h4>
      </div>
      <table class="timetable-table">
        <thead>
          <tr>
            <th>Period</th>
            <th>Time</th>
            <th>Subject</th>
            <th>Room</th>
            <th>Faculty</th>
            <th>Attendance</th>
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
    `;
    allDayOrdersContentEl.appendChild(block);
  });
}

function openAllDayOrdersModal() {
  allDayOrdersModal.classList.remove("is-hidden");
}

function closeAllDayOrdersModal() {
  allDayOrdersModal.classList.add("is-hidden");
}

function renderAttendance(subjects) {
  attendanceCardsEl.innerHTML = "";
  const totals = subjects.reduce((accumulator, subject) => {
    accumulator.attended += Number(subject.attendedClasses) || 0;
    accumulator.conducted += Number(subject.conductedClasses) || 0;
    return accumulator;
  }, { attended: 0, conducted: 0 });
  const overallPercentage = totals.conducted === 0 ? 0 : (totals.attended / totals.conducted) * 100;

  overallAttendanceValueEl.textContent = `${overallPercentage.toFixed(2)}%`;
  overallAttendanceMetaEl.textContent = `${totals.attended} / ${totals.conducted} classes attended`;

  if (!subjects.length) {
    attendanceCardsEl.innerHTML = `<div class="panel-card empty-card subtle-text">Attendance details are not available yet.</div>`;
    return;
  }

  subjects.forEach((subject, index) => {
    const metrics = computeAttendanceMetrics(subject);
    const fragment = attendanceCardTemplate.content.cloneNode(true);

    fragment.querySelector(".subject-name").textContent = `${metrics.subjectName} (${metrics.subjectCode})`;
    fragment.querySelector(".faculty-name").textContent = metrics.faculty;
    fragment.querySelector(".classes-summary").textContent = `${metrics.attendedClasses}/${metrics.conductedClasses} classes attended`;
    fragment.querySelector(".percentage-value").textContent = `${metrics.currentPercentage.toFixed(0)}%`;
    fragment.querySelector(".skip-value").textContent = `Can skip: ${metrics.classesCanSkip}`;
    fragment.querySelector(".needed-value").textContent = `Need: ${metrics.classesNeededForMinimum}`;

    const card = fragment.querySelector(".attendance-card");
    card.style.animationDelay = `${index * 80}ms`;
    applyRing(card, metrics.currentPercentage);
    attendanceCardsEl.appendChild(fragment);
  });
}

function renderMarks(marks, courseNameMap) {
  marksBodyEl.innerHTML = "";

  if (!marks.length) {
    marksBodyEl.innerHTML = `<tr><td colspan="4" class="subtle-text">Internal marks are not available yet.</td></tr>`;
    return;
  }

  marks.forEach((mark, index) => {
    const row = document.createElement("tr");
    row.className = "row-animate";
    row.style.animationDelay = `${index * 70}ms`;
    row.innerHTML = `
      <td>${courseNameMap.get(mark.subjectCode) || mark.subjectName} (${mark.subjectCode})</td>
      <td>${mark.courseType}</td>
      <td>${mark.assessments}</td>
      <td>${mark.total}</td>
    `;
    marksBodyEl.appendChild(row);
  });
}

function renderCourses(courses) {
  courseCardsEl.innerHTML = "";

  if (!courses.length) {
    courseCardsEl.innerHTML = `<div class="panel-card empty-card subtle-text">Course details are not available yet.</div>`;
    return;
  }

  courses.forEach((course, index) => {
    const card = document.createElement("article");
    card.className = "course-card animate-in";
    card.style.animationDelay = `${index * 80}ms`;
    card.innerHTML = `
      <p class="course-code">${course.subjectCode}</p>
      <h4>${course.subjectName}</h4>
      <p class="subtle-text">${course.faculty}</p>
      <div class="course-meta">
        <span>Credits: ${course.credits}</span>
        <span>Hours/Week: ${course.hoursPerWeek}</span>
      </div>
    `;
    courseCardsEl.appendChild(card);
  });
}

function renderPlanner(planner) {
  plannerBodyEl.innerHTML = "";

  if (!planner.length) {
    plannerBodyEl.innerHTML = `<tr><td colspan="4" class="subtle-text">Academic planner data is not available yet.</td></tr>`;
    return;
  }

  planner.forEach((entry, index) => {
    const row = document.createElement("tr");
    row.className = "row-animate";
    row.style.animationDelay = `${index * 70}ms`;
    row.innerHTML = `
      <td>${entry.displayDate || entry.date}</td>
      <td>${entry.weekday || ""}</td>
      <td>${entry.dayOrder}</td>
      <td>${entry.title}</td>
      <td>${entry.kind}</td>
    `;
    plannerBodyEl.appendChild(row);
  });
}

function renderCgpa(courses) {
  cgpaBodyEl.innerHTML = "";
  let totalWeightedPoints = 0;
  let totalCredits = 0;

  courses.forEach((course, index) => {
    const row = document.createElement("tr");
    row.className = "row-animate";
    row.style.animationDelay = `${index * 70}ms`;
    row.innerHTML = `
      <td>${course.subjectName}</td>
      <td>${course.credits}</td>
      <td>${course.grade}</td>
      <td>${course.points}</td>
    `;
    cgpaBodyEl.appendChild(row);
    totalWeightedPoints += course.credits * course.points;
    totalCredits += course.credits;
  });

  const cgpa = totalCredits === 0 ? 0 : totalWeightedPoints / totalCredits;
  cgpaValueEl.textContent = cgpa.toFixed(2);
}

function renderProfile(student) {
  const profileEntries = [
    ["Register Number", student.registrationNumber],
    ["Specialisation", student.specialisation],
    ["Department", student.department],
    ["Section", student.section],
    ["Faculty Advisor", student.facultyAdvisor],
    ["Mail ID", student.email],
    ["Program", student.program],
    ["Semester", student.semester],
    ["Batch", student.batch],
    ["Mobile", student.mobile],
    ["Class Room", student.classRoom]
  ];

  profileGridEl.innerHTML = "";
  profileEntries.forEach(([label, value], index) => {
    const block = document.createElement("div");
    block.className = "profile-item animate-in";
    block.style.animationDelay = `${index * 60}ms`;
    block.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    profileGridEl.appendChild(block);
  });
}

function activateSection(targetId) {
  sections.forEach((section) => {
    section.classList.toggle("is-hidden", section.id !== targetId);
    section.classList.toggle("section-enter", section.id === targetId);
  });
  menuButtons.forEach((button) => button.classList.toggle("is-active", button.dataset.target === targetId));
}

function computeAttendanceMetrics(subject) {
  const attendedClasses = Number(subject.attendedClasses);
  const conductedClasses = Number(subject.conductedClasses);
  const currentPercentage = conductedClasses === 0 ? 0 : (attendedClasses / conductedClasses) * 100;

  const classesNeededForMinimum = currentPercentage < MIN_ATTENDANCE_PERCENT
    ? Math.max(0, Math.ceil((MIN_ATTENDANCE_PERCENT * conductedClasses - 100 * attendedClasses) / (100 - MIN_ATTENDANCE_PERCENT)))
    : 0;

  const classesCanSkip = currentPercentage >= MIN_ATTENDANCE_PERCENT
    ? Math.max(0, Math.floor((100 * attendedClasses - MIN_ATTENDANCE_PERCENT * conductedClasses) / MIN_ATTENDANCE_PERCENT))
    : 0;

  return {
    ...subject,
    attendedClasses,
    conductedClasses,
    currentPercentage,
    classesNeededForMinimum,
    classesCanSkip
  };
}

function buildRingMarkup(percentage) {
  const dash = calculateRingDash(percentage);
  const ringClass = percentage >= MIN_ATTENDANCE_PERCENT ? "ring-safe" : "ring-danger";
  return `
    <div class="radial-wrap radial-wrap-small">
      <svg class="attendance-ring" viewBox="0 0 120 120" aria-hidden="true">
        <circle class="attendance-ring-track" cx="60" cy="60" r="42"></circle>
        <circle class="attendance-ring-progress ${ringClass}" cx="60" cy="60" r="42" style="stroke-dasharray:${dash}"></circle>
      </svg>
      <div class="ring-label ring-label-small">${percentage.toFixed(0)}%</div>
    </div>
  `;
}

function applyRing(card, percentage) {
  const ring = card.querySelector(".attendance-ring-progress");
  const label = card.querySelector(".percentage-value");
  ring.style.strokeDasharray = calculateRingDash(percentage);
  ring.classList.add(percentage >= MIN_ATTENDANCE_PERCENT ? "ring-safe" : "ring-danger");
  label.classList.toggle("is-safe", percentage >= MIN_ATTENDANCE_PERCENT);
  label.classList.toggle("is-danger", percentage < MIN_ATTENDANCE_PERCENT);
}

function calculateRingDash(percentage) {
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const clampedPercentage = Math.max(0, Math.min(100, percentage));
  const progress = (clampedPercentage / 100) * circumference;
  return `${progress} ${circumference}`;
}

function formatDateTime(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function isToday(value) {
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.getTime())) {
    const now = new Date();
    return parsed.toDateString() === now.toDateString();
  }

  const today = new Date();
  const tokens = [
    today.toLocaleDateString("en-GB"),
    today.toLocaleDateString("en-IN"),
    today.toISOString().slice(0, 10),
    today.toLocaleDateString("en-US", { day: "2-digit", month: "short", year: "numeric" }),
    today.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "2-digit" })
  ].map((token) => token.toLowerCase());

  const normalizedValue = String(value || "").toLowerCase();
  return tokens.some((token) => normalizedValue.includes(token));
}

function updateThemeFromControls() {
  const theme = {
    accent: accentPicker.value,
    text: textPicker.value,
    panel: panelPicker.value
  };

  applyTheme(theme);
  window.localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(theme));
}

function resetTheme() {
  applyTheme(DEFAULT_THEME);
  accentPicker.value = DEFAULT_THEME.accent;
  textPicker.value = DEFAULT_THEME.text;
  panelPicker.value = DEFAULT_THEME.panel;
  window.localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(DEFAULT_THEME));
}

function applyStoredTheme() {
  const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (!storedTheme) {
    applyTheme(DEFAULT_THEME);
    return;
  }

  try {
    const parsedTheme = JSON.parse(storedTheme);
    applyTheme(parsedTheme);
  } catch (error) {
    applyTheme(DEFAULT_THEME);
  }
}

function applyTheme(theme) {
  document.documentElement.style.setProperty("--accent", theme.accent);
  document.documentElement.style.setProperty("--text", theme.text);
  document.documentElement.style.setProperty("--panel", theme.panel);
  accentPicker.value = theme.accent;
  textPicker.value = theme.text;
  panelPicker.value = theme.panel;
}
