/* Refract SCORM package — navigation + interactive blocks (vanilla JS).
 * Drives a single-SCO index page: lesson navigation, knowledge checks,
 * scenarios, and a graded quiz, reporting completion + score through the
 * window.Scorm shim (scorm-api.js). Works fully offline in preview mode.
 */
(function () {
  "use strict";

  var Scorm = window.Scorm;
  var lessons = Array.prototype.slice.call(document.querySelectorAll(".lesson"));
  var total = lessons.length;
  var quizForms = Array.prototype.slice.call(document.querySelectorAll(".quiz"));
  var finalQuizId = quizForms.length ? quizForms[quizForms.length - 1].dataset.quizId : null;

  var visited = {};        // { index: true }
  var quizResults = {};    // { quizId: { passed: bool, score: number } }
  var current = 0;

  // ---------------------------------------------------------------- helpers

  function visitedCount() { return Object.keys(visited).length; }

  function allVisited() { return visitedCount() >= total; }

  function allQuizzesPassed() {
    for (var i = 0; i < quizForms.length; i++) {
      var id = quizForms[i].dataset.quizId;
      if (!quizResults[id] || !quizResults[id].passed) return false;
    }
    return true;
  }

  // ---------------------------------------------------------------- SCORM state

  function loadState() {
    var raw = Scorm.get("cmi.suspend_data");
    if (raw) {
      try {
        var s = JSON.parse(raw);
        if (s && typeof s === "object") {
          (s.completed_lessons || []).forEach(function (i) { visited[i] = true; });
          quizResults = s.quiz || {};
          if (typeof s.lesson_index === "number") current = s.lesson_index;
        }
      } catch (e) { /* corrupt suspend data: start fresh */ }
    }
    var loc = Scorm.get("cmi.core.lesson_location");
    if (loc !== "" && loc != null) {
      var n = parseInt(loc, 10);
      if (!isNaN(n)) current = n;
    }
    if (current < 0 || current >= total) current = 0;

    // First launch: mark incomplete (don't clobber a prior passed/completed).
    var status = Scorm.get("cmi.core.lesson_status");
    if (status !== "passed" && status !== "completed" && status !== "failed") {
      Scorm.set("cmi.core.lesson_status", "incomplete");
    }
  }

  function persist() {
    var payload = {
      v: 1,
      lesson_index: current,
      completed_lessons: Object.keys(visited).map(Number),
      quiz: quizResults
    };
    Scorm.set("cmi.suspend_data", JSON.stringify(payload));
    Scorm.set("cmi.core.lesson_location", String(current));
    evaluateCompletion();
    Scorm.commit();
  }

  function evaluateCompletion() {
    if (finalQuizId) {
      if (allVisited() && allQuizzesPassed()) {
        var final = quizResults[finalQuizId];
        Scorm.set("cmi.core.lesson_status", "passed");
        if (final && typeof final.score === "number") {
          Scorm.set("cmi.core.score.min", "0");
          Scorm.set("cmi.core.score.max", "100");
          Scorm.set("cmi.core.score.raw", String(Math.round(final.score)));
        }
      } else {
        var st = Scorm.get("cmi.core.lesson_status");
        if (st !== "passed") Scorm.set("cmi.core.lesson_status", "incomplete");
      }
    } else {
      Scorm.set("cmi.core.lesson_status", allVisited() ? "completed" : "incomplete");
    }
    updateCourseDone();
  }

  // ---------------------------------------------------------------- navigation

  var progressBar = document.querySelector(".progress-bar");
  var visitedLabel = document.querySelector(".visited-count");
  var indicator = document.querySelector(".lesson-indicator");
  var prevBtn = document.querySelector(".nav-prev");
  var nextBtn = document.querySelector(".nav-next");
  var menuItems = Array.prototype.slice.call(document.querySelectorAll(".menu-item"));
  var courseDoneEl = document.querySelector(".course-done");

  function updateProgress() {
    var pct = total ? (visitedCount() / total) * 100 : 0;
    if (progressBar) progressBar.style.width = pct + "%";
    if (visitedLabel) visitedLabel.textContent = String(visitedCount());
  }

  function updateCourseDone() {
    if (!courseDoneEl) return;
    var done = finalQuizId ? (allVisited() && allQuizzesPassed()) : allVisited();
    courseDoneEl.hidden = !done;
  }

  function updateMenu() {
    menuItems.forEach(function (item) {
      var idx = Number(item.dataset.index);
      var isCurrent = idx === current;
      item.setAttribute("aria-current", isCurrent ? "true" : "false");
      var status = item.querySelector(".menu-status");
      if (visited[idx]) {
        item.classList.add("visited");
        if (status) status.textContent = "Visited";
      } else if (status && !isCurrent) {
        status.textContent = "Not visited";
      }
      if (status && isCurrent && !visited[idx]) status.textContent = "Current";
    });
  }

  function showLesson(i, focus) {
    if (i < 0 || i >= total) return;
    current = i;
    lessons.forEach(function (el, idx) { el.hidden = idx !== i; });
    visited[i] = true;

    if (prevBtn) prevBtn.hidden = i === 0;
    if (nextBtn) nextBtn.hidden = i === total - 1;
    if (indicator) indicator.textContent = "Lesson " + (i + 1) + " of " + total;

    updateProgress();
    updateMenu();
    persist();

    if (focus !== false) {
      var h = lessons[i].querySelector("h2");
      if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
    }
    if (typeof window.scrollTo === "function") window.scrollTo(0, 0);
  }

  if (prevBtn) prevBtn.addEventListener("click", function () { showLesson(current - 1); });
  if (nextBtn) nextBtn.addEventListener("click", function () { showLesson(current + 1); });
  menuItems.forEach(function (item) {
    item.addEventListener("click", function () { showLesson(Number(item.dataset.index)); });
  });

  // ---------------------------------------------------------------- knowledge check

  document.querySelectorAll(".kc").forEach(function (block) {
    var feedback = block.querySelector(".feedback");
    var opts = Array.prototype.slice.call(block.querySelectorAll(".opt"));
    opts.forEach(function (btn) {
      btn.addEventListener("click", function () {
        opts.forEach(function (o) { o.setAttribute("aria-checked", "false"); });
        btn.setAttribute("aria-checked", "true");
        var correct = btn.dataset.correct === "true";
        feedback.className = "feedback " + (correct ? "correct" : "incorrect");
        feedback.textContent = (correct ? "Correct. " : "Not quite. ") + (btn.dataset.feedback || "");
        feedback.hidden = false;
      });
    });
  });

  // ---------------------------------------------------------------- scenario

  document.querySelectorAll(".scenario").forEach(function (block) {
    var feedback = block.querySelector(".feedback");
    var consequences = block.querySelector(".consequences");
    var choices = Array.prototype.slice.call(block.querySelectorAll(".choice"));
    var verdicts = { best: "Best choice.", acceptable: "Workable, not ideal.", poor: "Risky choice." };

    function pick(btn) {
      var cid = btn.dataset.cid;
      var quality = btn.dataset.quality;
      choices.forEach(function (c) { c.setAttribute("aria-pressed", "false"); });
      btn.setAttribute("aria-pressed", "true");

      var src = consequences.querySelector('[data-cid="' + cid + '"]');
      feedback.className = "feedback " + quality;
      feedback.innerHTML = "";
      var verdict = document.createElement("span");
      verdict.className = "verdict";
      verdict.textContent = verdicts[quality] || "";
      feedback.appendChild(verdict);
      feedback.appendChild(document.createTextNode(" "));
      if (src) {
        var body = document.createElement("span");
        body.className = "prose";
        body.innerHTML = src.innerHTML;
        feedback.appendChild(body);
      }
      if (quality !== "best") {
        var again = document.createElement("button");
        again.type = "button";
        again.className = "try-again";
        again.textContent = "Try another approach";
        again.addEventListener("click", function () {
          feedback.hidden = true;
          feedback.innerHTML = "";
          choices.forEach(function (c) { c.setAttribute("aria-pressed", "false"); });
          choices[0].focus();
        });
        feedback.appendChild(document.createElement("br"));
        feedback.appendChild(again);
      }
      feedback.hidden = false;
    }

    choices.forEach(function (btn) {
      btn.addEventListener("click", function () { pick(btn); });
    });
  });

  // ---------------------------------------------------------------- quiz

  function lockQuiz(form, score, passed) {
    var passPct = Number(form.dataset.passPct);
    form.querySelectorAll(".quiz-q").forEach(function (q) {
      q.querySelectorAll(".opt").forEach(function (btn) {
        var correct = btn.dataset.correct === "true";
        var chosen = btn.getAttribute("aria-pressed") === "true";
        btn.disabled = true;
        if (correct) btn.classList.add("correct");
        else if (chosen) btn.classList.add("incorrect");
      });
      q.querySelectorAll(".rationale").forEach(function (r) {
        var opt = r.previousElementSibling;
        var correct = opt && opt.dataset.correct === "true";
        var chosen = opt && opt.getAttribute("aria-pressed") === "true";
        if (correct || chosen) r.hidden = false;
      });
    });
    var submitBtn = form.querySelector(".submit-quiz");
    if (submitBtn) submitBtn.disabled = true;
    var result = form.querySelector(".quiz-result");
    result.className = "quiz-result " + (passed ? "pass" : "fail");
    result.textContent = score + "% — " + (passed ? "Passed" : "Below the " + passPct + "% passing score");
    result.hidden = false;
  }

  quizForms.forEach(function (form) {
    var multiToggle = function (btn, q) {
      var multi = q.dataset.multi === "true";
      if (multi) {
        var on = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", on ? "false" : "true");
      } else {
        q.querySelectorAll(".opt").forEach(function (o) { o.setAttribute("aria-pressed", "false"); });
        btn.setAttribute("aria-pressed", "true");
      }
    };

    form.querySelectorAll(".quiz-q").forEach(function (q) {
      q.querySelectorAll(".opt").forEach(function (btn) {
        btn.addEventListener("click", function () { multiToggle(btn, q); });
      });
    });

    var submitBtn = form.querySelector(".submit-quiz");
    submitBtn.addEventListener("click", function () {
      var questions = Array.prototype.slice.call(form.querySelectorAll(".quiz-q"));
      var correctCount = 0;
      questions.forEach(function (q) {
        var chosen = {}, right = {}, chosenN = 0, rightN = 0, exact = true;
        q.querySelectorAll(".opt").forEach(function (btn) {
          var oid = btn.dataset.oid;
          if (btn.getAttribute("aria-pressed") === "true") { chosen[oid] = true; chosenN++; }
          if (btn.dataset.correct === "true") { right[oid] = true; rightN++; }
        });
        if (chosenN !== rightN) exact = false;
        else { for (var k in chosen) { if (!right[k]) { exact = false; break; } } }
        if (exact && rightN > 0) correctCount++;
      });
      var score = Math.round((correctCount / questions.length) * 1000) / 10;
      var passPct = Number(form.dataset.passPct);
      var passed = score >= passPct;
      quizResults[form.dataset.quizId] = { passed: passed, score: score };
      lockQuiz(form, score, passed);
      persist();
    });
  });

  // Restore previously-submitted quizzes (resume).
  quizForms.forEach(function (form) {
    var prior = quizResults[form.dataset.quizId];
    if (prior) lockQuiz(form, prior.score, prior.passed);
  });

  // ---------------------------------------------------------------- boot

  Scorm.init();
  var badge = document.querySelector(".preview-badge");
  if (badge && Scorm.previewMode) badge.hidden = false;

  loadState();
  showLesson(current, false);

  window.addEventListener("beforeunload", function () { Scorm.finish(); });
})();
