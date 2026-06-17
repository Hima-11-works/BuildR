// ──────────────────────────────────────────────────────────────
// static/app.js — Client-side logic for the BuildR profile editor
// ──────────────────────────────────────────────────────────────
//
// ARCHITECTURE OVERVIEW
// ---------------------
// This file handles all client-side behavior:
//   1. Loading the profile from the API on page load
//   2. Populating the form with the loaded data
//   3. Dynamic add/remove of list items (education, experience, etc.)
//   4. Collecting form data into a JSON object matching the Profile schema
//   5. Saving to the API and handling validation errors
//
// HOW fetch() TALKS TO FLASK
// --------------------------
// When the browser loads this page from http://localhost:5000,
// all fetch() calls go to the SAME origin (host:port).
//
//   fetch('/api/profile')
//     → Browser sends:  GET http://localhost:5000/api/profile
//     → Flask's @app.route('/api/profile', methods=['GET']) catches it
//     → Flask returns JSON with Content-Type: application/json
//     → We call response.json() to parse it into a JS object
//
//   fetch('/api/profile', { method: 'PUT', body: JSON.stringify(data) })
//     → Browser sends:  PUT http://localhost:5000/api/profile
//     → The Content-Type header tells Flask it's JSON
//     → Flask's request.get_json() parses the body into a Python dict
//     → Pydantic validates the dict → Flask returns success or errors
//
// THE DOM IS THE SOURCE OF TRUTH
// ------------------------------
// We do NOT maintain a separate JavaScript "state" object that
// mirrors the form.  Instead:
//   • To SAVE: we walk the DOM, read every input, and build the
//     JSON object on the fly (collectFormData()).
//   • To LOAD: we walk the JSON and create/fill DOM elements.
//
// This keeps things simple — there's no "sync" problem between
// a JS object and the form.  The form IS the data.
// ──────────────────────────────────────────────────────────────

"use strict";

// ── DOM references ──────────────────────────────────────────
const saveBtn         = document.getElementById("save-btn");
const saveBtnText     = document.getElementById("save-btn-text");
const errorSummary    = document.getElementById("error-summary");
const errorList       = document.getElementById("error-list");
const toastContainer  = document.getElementById("toast-container");

// List containers
const educationList     = document.getElementById("education-list");
const experienceList    = document.getElementById("experience-list");
const projectList       = document.getElementById("project-list");
const certificationList = document.getElementById("certification-list");
const skillsList        = document.getElementById("skills-list");


// ═══════════════════════════════════════════════════════════════
// 1. LOAD PROFILE — fetch from API and populate the form
// ═══════════════════════════════════════════════════════════════

async function loadProfile() {
    try {
        const response = await fetch("/api/profile");
        if (!response.ok) throw new Error(`Server error: ${response.status}`);

        const profile = await response.json();

        // Check for load errors (corrupt file, etc.)
        if (profile.error) {
            showToast(profile.error, "error");
            return;
        }

        populateForm(profile);
    } catch (err) {
        showToast(`Failed to load profile: ${err.message}`, "error");
    }
}

/**
 * Walk the profile object and fill in every form section.
 *
 * For simple fields (personal_info), we just set .value on inputs.
 * For lists (education, experience, etc.), we loop and call the
 * corresponding addXxxItem(data) function for each entry.
 */
function populateForm(profile) {
    // ── Personal Info ────────────────────────────────────────
    const pi = profile.personal_info || {};
    document.getElementById("pi-name").value = pi.name || "";
    document.getElementById("pi-email").value = pi.email || "";
    document.getElementById("pi-phone").value = pi.phone || "";

    // Links (key-value pairs)
    const linksContainer = document.getElementById("links-list");
    linksContainer.innerHTML = "";
    if (pi.links && Object.keys(pi.links).length > 0) {
        for (const [label, url] of Object.entries(pi.links)) {
            addLinkRow(label, url);
        }
    }

    // ── Education ────────────────────────────────────────────
    educationList.innerHTML = "";
    (profile.education || []).forEach(edu => addEducationItem(edu));

    // ── Experience ───────────────────────────────────────────
    experienceList.innerHTML = "";
    (profile.experience || []).forEach(exp => addExperienceItem(exp));

    // ── Projects ─────────────────────────────────────────────
    projectList.innerHTML = "";
    (profile.projects || []).forEach(proj => addProjectItem(proj));

    // ── Skills ───────────────────────────────────────────────
    skillsList.innerHTML = "";
    const cats = (profile.skills && profile.skills.categories) || {};
    for (const [catName, skills] of Object.entries(cats)) {
        addSkillCategory(catName, skills);
    }

    // ── Certifications ───────────────────────────────────────
    certificationList.innerHTML = "";
    (profile.certifications || []).forEach(cert => addCertificationItem(cert));
}


// ═══════════════════════════════════════════════════════════════
// 2. DYNAMIC LIST MANAGEMENT — add/remove items
// ═══════════════════════════════════════════════════════════════
//
// THE PATTERN (repeated for every list section):
//
//   function addXxxItem(data?) {
//     1. Build DOM elements (a .list-item div with inputs inside)
//     2. If `data` is provided → fill in the inputs (loading saved data)
//        If `data` is null   → leave blank (user clicking "+ Add")
//     3. Attach a "Remove" button → removes this .list-item from DOM
//     4. Append to the section's container
//     5. Update item numbering
//   }
//
// This is intentionally repetitive rather than over-abstracted.
// Each section has different fields, so a generic builder would
// actually be MORE complex to read and maintain.
// ═══════════════════════════════════════════════════════════════


// ── Links (key-value rows) ───────────────────────────────────

function addLinkRow(label, url) {
    const container = document.getElementById("links-list");
    const row = document.createElement("div");
    row.className = "kv-row";
    row.innerHTML = `
        <input type="text" placeholder="Label (e.g. GitHub)" class="link-label" value="${escapeAttr(label || "")}">
        <input type="text" placeholder="https://..." class="link-url" value="${escapeAttr(url || "")}">
        <button type="button" class="btn-remove" title="Remove link">✕</button>
    `;
    row.querySelector(".btn-remove").addEventListener("click", () => {
        row.remove();
    });
    container.appendChild(row);
}


// ── Education ────────────────────────────────────────────────

function addEducationItem(data) {
    const item = document.createElement("div");
    item.className = "list-item education-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove">✕ Remove</button>
        </div>
        <div class="form-grid">
            <div class="form-group">
                <label>Institution</label>
                <input type="text" class="edu-institution" placeholder="e.g. MIT" value="${escapeAttr(data?.institution || "")}">
            </div>
            <div class="form-group">
                <label>Degree</label>
                <input type="text" class="edu-degree" placeholder="e.g. B.S. Computer Science" value="${escapeAttr(data?.degree || "")}">
            </div>
            <div class="form-group">
                <label>Start Date</label>
                <input type="text" class="edu-start-date" placeholder="e.g. Aug 2020" value="${escapeAttr(data?.start_date || "")}">
            </div>
            <div class="form-group">
                <label>End Date</label>
                <input type="text" class="edu-end-date" placeholder="e.g. May 2024 or Present" value="${escapeAttr(data?.end_date || "")}">
            </div>
            <div class="form-group">
                <label>GPA (optional)</label>
                <input type="text" class="edu-gpa" placeholder="e.g. 3.8" value="${data?.gpa != null ? data.gpa : ""}">
            </div>
            <div class="form-group">
                <label>Coursework (optional)</label>
                <input type="text" class="edu-coursework" placeholder="Comma-separated, e.g. Data Structures, Algorithms" value="${escapeAttr((data?.coursework || []).join(", "))}">
            </div>
        </div>
    `;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        item.remove();
        renumberItems(educationList, "Education");
    });

    educationList.appendChild(item);
    renumberItems(educationList, "Education");
}


// ── Experience ───────────────────────────────────────────────

function addExperienceItem(data) {
    const item = document.createElement("div");
    item.className = "list-item experience-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove">✕ Remove</button>
        </div>
        <div class="form-grid">
            <div class="form-group">
                <label>Company</label>
                <input type="text" class="exp-company" placeholder="e.g. Google" value="${escapeAttr(data?.company || "")}">
            </div>
            <div class="form-group">
                <label>Role</label>
                <input type="text" class="exp-role" placeholder="e.g. Software Engineer" value="${escapeAttr(data?.role || "")}">
            </div>
            <div class="form-group">
                <label>Start Date</label>
                <input type="text" class="exp-start-date" placeholder="e.g. Jun 2022" value="${escapeAttr(data?.start_date || "")}">
            </div>
            <div class="form-group">
                <label>End Date</label>
                <input type="text" class="exp-end-date" placeholder="e.g. Present" value="${escapeAttr(data?.end_date || "")}">
            </div>
            <div class="form-group full-width">
                <label>Bullet Points (one per line)</label>
                <textarea class="exp-bullets" rows="4" placeholder="e.g.&#10;Built a microservice that reduced latency by 40%&#10;Led a team of 3 engineers on the search feature">${escapeHtml((data?.bullets || []).join("\n"))}</textarea>
            </div>
            <div class="form-group full-width">
                <label>Technologies (comma-separated)</label>
                <input type="text" class="exp-technologies" placeholder="e.g. Python, Flask, PostgreSQL, Docker" value="${escapeAttr((data?.technologies || []).join(", "))}">
            </div>
        </div>
    `;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        item.remove();
        renumberItems(experienceList, "Experience");
    });

    experienceList.appendChild(item);
    renumberItems(experienceList, "Experience");
}


// ── Projects ─────────────────────────────────────────────────

function addProjectItem(data) {
    const item = document.createElement("div");
    item.className = "list-item project-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove">✕ Remove</button>
        </div>
        <div class="form-grid">
            <div class="form-group">
                <label>Project Name</label>
                <input type="text" class="proj-name" placeholder="e.g. ChatBot AI" value="${escapeAttr(data?.name || "")}">
            </div>
            <div class="form-group">
                <label>Link (optional)</label>
                <input type="text" class="proj-link" placeholder="https://github.com/..." value="${escapeAttr(data?.link || "")}">
            </div>
            <div class="form-group full-width">
                <label>Description</label>
                <input type="text" class="proj-description" placeholder="One-line summary of the project" value="${escapeAttr(data?.description || "")}">
            </div>
            <div class="form-group full-width">
                <label>Key Highlights (one per line)</label>
                <textarea class="proj-bullets" rows="3" placeholder="e.g.&#10;Implemented real-time chat with WebSockets&#10;Achieved 95% test coverage">${escapeHtml((data?.bullets || []).join("\n"))}</textarea>
            </div>
            <div class="form-group full-width">
                <label>Technologies (comma-separated)</label>
                <input type="text" class="proj-technologies" placeholder="e.g. React, Node.js, MongoDB" value="${escapeAttr((data?.technologies || []).join(", "))}">
            </div>
        </div>
    `;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        item.remove();
        renumberItems(projectList, "Project");
    });

    projectList.appendChild(item);
    renumberItems(projectList, "Project");
}


// ── Certifications ───────────────────────────────────────────

function addCertificationItem(data) {
    const item = document.createElement("div");
    item.className = "list-item certification-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove">✕ Remove</button>
        </div>
        <div class="form-grid">
            <div class="form-group">
                <label>Certification Name</label>
                <input type="text" class="cert-name" placeholder="e.g. AWS Solutions Architect" value="${escapeAttr(data?.name || "")}">
            </div>
            <div class="form-group">
                <label>Issuer</label>
                <input type="text" class="cert-issuer" placeholder="e.g. Amazon Web Services" value="${escapeAttr(data?.issuer || "")}">
            </div>
            <div class="form-group">
                <label>Date</label>
                <input type="text" class="cert-date" placeholder="e.g. Mar 2024" value="${escapeAttr(data?.date || "")}">
            </div>
        </div>
    `;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        item.remove();
        renumberItems(certificationList, "Certification");
    });

    certificationList.appendChild(item);
    renumberItems(certificationList, "Certification");
}


// ── Skills ───────────────────────────────────────────────────

function addSkillCategory(catName, skills) {
    const cat = document.createElement("div");
    cat.className = "skill-category";

    cat.innerHTML = `
        <div class="skill-category-header">
            <input type="text" class="skill-cat-name" placeholder="Category name (e.g. Languages)" value="${escapeAttr(catName || "")}">
            <button type="button" class="btn-remove" title="Remove category">✕</button>
        </div>
        <div class="skill-tags"></div>
        <div class="skill-add-input" style="margin-top: var(--space-sm);">
            <input type="text" class="skill-new-input" placeholder="Add skill…">
            <button type="button" title="Add skill">+</button>
        </div>
    `;

    // Remove entire category
    cat.querySelector(".skill-category-header .btn-remove").addEventListener("click", () => {
        cat.remove();
    });

    // Add skill tag
    const tagsContainer = cat.querySelector(".skill-tags");
    const newInput = cat.querySelector(".skill-new-input");
    const addBtn = cat.querySelector(".skill-add-input button");

    function addSkillTag(skillName) {
        if (!skillName.trim()) return;
        const tag = document.createElement("span");
        tag.className = "skill-tag";
        tag.innerHTML = `
            <span class="skill-tag-text">${escapeHtml(skillName.trim())}</span>
            <button type="button" class="skill-tag-remove" title="Remove">×</button>
        `;
        tag.querySelector(".skill-tag-remove").addEventListener("click", () => {
            tag.remove();
        });
        tagsContainer.appendChild(tag);
    }

    addBtn.addEventListener("click", () => {
        addSkillTag(newInput.value);
        newInput.value = "";
        newInput.focus();
    });

    newInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            addSkillTag(newInput.value);
            newInput.value = "";
        }
    });

    // Populate existing skills
    (skills || []).forEach(s => addSkillTag(s));

    skillsList.appendChild(cat);
}


// ── Renumber items ───────────────────────────────────────────

function renumberItems(container, label) {
    const items = container.querySelectorAll(".list-item");
    items.forEach((item, i) => {
        const num = item.querySelector(".list-item-number");
        if (num) num.textContent = `${label} #${i + 1}`;
    });
}


// ═══════════════════════════════════════════════════════════════
// 3. COLLECT FORM DATA — DOM → JSON object
// ═══════════════════════════════════════════════════════════════
//
// This walks every section of the form, reads input values, and
// builds a plain JS object whose shape matches the Pydantic
// Profile model exactly.  This object is then JSON.stringify()'d
// and sent to PUT /api/profile.
// ═══════════════════════════════════════════════════════════════

function collectFormData() {
    const profile = {};

    // ── Personal Info ────────────────────────────────────────
    const links = {};
    document.querySelectorAll("#links-list .kv-row").forEach(row => {
        const label = row.querySelector(".link-label").value.trim();
        const url   = row.querySelector(".link-url").value.trim();
        if (label) links[label] = url;
    });

    profile.personal_info = {
        name:  document.getElementById("pi-name").value,
        email: document.getElementById("pi-email").value,
        phone: document.getElementById("pi-phone").value || null,
        links: links,
    };

    // ── Education ────────────────────────────────────────────
    profile.education = [];
    educationList.querySelectorAll(".education-item").forEach(item => {
        const gpaTxt = item.querySelector(".edu-gpa").value.trim();
        const courseworkTxt = item.querySelector(".edu-coursework").value.trim();

        profile.education.push({
            institution: item.querySelector(".edu-institution").value,
            degree:      item.querySelector(".edu-degree").value,
            start_date:  item.querySelector(".edu-start-date").value,
            end_date:    item.querySelector(".edu-end-date").value,
            gpa:         gpaTxt ? parseFloat(gpaTxt) : null,
            coursework:  courseworkTxt ? courseworkTxt.split(",").map(s => s.trim()).filter(Boolean) : [],
        });
    });

    // ── Experience ───────────────────────────────────────────
    profile.experience = [];
    experienceList.querySelectorAll(".experience-item").forEach(item => {
        const bulletsTxt = item.querySelector(".exp-bullets").value;
        const techTxt    = item.querySelector(".exp-technologies").value.trim();

        profile.experience.push({
            company:      item.querySelector(".exp-company").value,
            role:         item.querySelector(".exp-role").value,
            start_date:   item.querySelector(".exp-start-date").value,
            end_date:     item.querySelector(".exp-end-date").value,
            bullets:      bulletsTxt.split("\n").map(s => s.trim()).filter(Boolean),
            technologies: techTxt ? techTxt.split(",").map(s => s.trim()).filter(Boolean) : [],
        });
    });

    // ── Projects ─────────────────────────────────────────────
    profile.projects = [];
    projectList.querySelectorAll(".project-item").forEach(item => {
        const bulletsTxt = item.querySelector(".proj-bullets").value;
        const techTxt    = item.querySelector(".proj-technologies").value.trim();
        const linkVal    = item.querySelector(".proj-link").value.trim();

        profile.projects.push({
            name:         item.querySelector(".proj-name").value,
            description:  item.querySelector(".proj-description").value,
            bullets:      bulletsTxt.split("\n").map(s => s.trim()).filter(Boolean),
            technologies: techTxt ? techTxt.split(",").map(s => s.trim()).filter(Boolean) : [],
            link:         linkVal || null,
        });
    });

    // ── Skills ───────────────────────────────────────────────
    const categories = {};
    skillsList.querySelectorAll(".skill-category").forEach(cat => {
        const catName = cat.querySelector(".skill-cat-name").value.trim();
        if (!catName) return;  // skip unnamed categories
        const skillNames = [];
        cat.querySelectorAll(".skill-tag-text").forEach(tag => {
            skillNames.push(tag.textContent.trim());
        });
        categories[catName] = skillNames;
    });
    profile.skills = { categories };

    // ── Certifications ───────────────────────────────────────
    profile.certifications = [];
    certificationList.querySelectorAll(".certification-item").forEach(item => {
        profile.certifications.push({
            name:   item.querySelector(".cert-name").value,
            issuer: item.querySelector(".cert-issuer").value,
            date:   item.querySelector(".cert-date").value,
        });
    });

    return profile;
}


// ═══════════════════════════════════════════════════════════════
// 4. SAVE PROFILE — collect data and PUT to API
// ═══════════════════════════════════════════════════════════════

async function saveProfile() {
    // ── Clear previous errors ────────────────────────────────
    clearErrors();

    // ── Collect data from the DOM ────────────────────────────
    const data = collectFormData();

    // ── Show loading state ───────────────────────────────────
    saveBtn.disabled = true;
    saveBtnText.textContent = "Saving…";

    try {
        // ── Send to Flask ────────────────────────────────────
        //
        // fetch() is the modern browser API for HTTP requests.
        //   method: 'PUT'  → matches Flask's @app.route methods=['PUT']
        //   headers         → tells Flask the body is JSON
        //   body            → the JSON string Flask will parse
        //
        // The response is an HTTP response object.  We call
        // .json() to parse the body as JSON.
        const response = await fetch("/api/profile", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });

        const result = await response.json();

        if (response.ok) {
            // ── Success! ─────────────────────────────────────
            showToast("Profile saved successfully!", "success");
        } else if (response.status === 422 && result.errors) {
            // ── Validation errors from Pydantic ──────────────
            displayErrors(result.errors);
            showToast("Please fix the validation errors below.", "error");
        } else {
            // ── Other server error ───────────────────────────
            showToast(result.error || "Something went wrong.", "error");
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, "error");
    } finally {
        saveBtn.disabled = false;
        saveBtnText.textContent = "Save Profile";
    }
}


// ═══════════════════════════════════════════════════════════════
// 5. VALIDATION ERROR DISPLAY
// ═══════════════════════════════════════════════════════════════
//
// Pydantic errors arrive as an array like:
//   [
//     { loc: ["education", 0, "institution"], msg: "String should have at least 1 character", type: "string_too_short" },
//     { loc: ["personal_info", "name"],       msg: "Field required",                         type: "missing" }
//   ]
//
// The `loc` array is a path — we can use it to find the
// corresponding form field and highlight it.
// ═══════════════════════════════════════════════════════════════

function displayErrors(errors) {
    // ── Show error summary banner ────────────────────────────
    errorList.innerHTML = "";
    errors.forEach(err => {
        const li = document.createElement("li");
        const path = err.loc.join(" → ");
        li.textContent = `${path}: ${err.msg}`;
        errorList.appendChild(li);
    });
    errorSummary.classList.add("visible");

    // ── Highlight individual fields ──────────────────────────
    // Map loc paths to CSS classes on form inputs.
    // This is a best-effort mapping — not every error will
    // have a matching field, but the summary always shows all.
    errors.forEach(err => {
        const input = locToInput(err.loc);
        if (input) {
            input.classList.add("has-error");
            // Show inline error message
            const errorSpan = input.parentElement.querySelector(".field-error");
            if (errorSpan) {
                errorSpan.textContent = err.msg;
                errorSpan.classList.add("visible");
            }
        }
    });

    // Scroll to error summary
    errorSummary.scrollIntoView({ behavior: "smooth", block: "center" });
}

/**
 * Map a Pydantic error `loc` path to the corresponding DOM input.
 *
 * Examples:
 *   ["personal_info", "name"]        → #pi-name
 *   ["education", 0, "institution"]  → .education-item:nth(0) .edu-institution
 *   ["experience", 2, "company"]     → .experience-item:nth(2) .exp-company
 */
function locToInput(loc) {
    if (loc.length < 2) return null;

    const section = loc[0];

    // ── Personal info fields ─────────────────────────────────
    if (section === "personal_info") {
        const fieldMap = { name: "pi-name", email: "pi-email", phone: "pi-phone" };
        return document.getElementById(fieldMap[loc[1]]) || null;
    }

    // ── List sections (education, experience, projects, certifications)
    if (loc.length >= 3 && typeof loc[1] === "number") {
        const index = loc[1];
        const field = loc[2];
        const sectionMap = {
            education:      { container: educationList,     itemClass: "education-item",     prefix: "edu" },
            experience:     { container: experienceList,    itemClass: "experience-item",    prefix: "exp" },
            projects:       { container: projectList,       itemClass: "project-item",       prefix: "proj" },
            certifications: { container: certificationList, itemClass: "certification-item", prefix: "cert" },
        };

        const cfg = sectionMap[section];
        if (!cfg) return null;

        const items = cfg.container.querySelectorAll(`.${cfg.itemClass}`);
        if (index >= items.length) return null;

        // Build the class name: education.institution → .edu-institution
        const fieldMap = {
            // Education
            institution: "institution", degree: "degree",
            start_date: "start-date", end_date: "end-date",
            gpa: "gpa", coursework: "coursework",
            // Experience
            company: "company", role: "role",
            bullets: "bullets", technologies: "technologies",
            // Projects
            name: "name", description: "description",
            link: "link",
            // Certifications
            issuer: "issuer", date: "date",
        };

        const mappedField = fieldMap[field] || field;
        return items[index].querySelector(`.${cfg.prefix}-${mappedField}`) || null;
    }

    return null;
}

function clearErrors() {
    errorSummary.classList.remove("visible");
    errorList.innerHTML = "";
    document.querySelectorAll(".has-error").forEach(el => el.classList.remove("has-error"));
    document.querySelectorAll(".field-error.visible").forEach(el => {
        el.classList.remove("visible");
        el.textContent = "";
    });
}


// ═══════════════════════════════════════════════════════════════
// 6. TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════

function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span>${type === "success" ? "✓" : "⚠"}</span>
        <span>${escapeHtml(message)}</span>
    `;
    toastContainer.appendChild(toast);

    // Auto-dismiss after 4 seconds
    setTimeout(() => {
        toast.style.animation = "toastOut 300ms ease-in forwards";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}


// ═══════════════════════════════════════════════════════════════
// 7. UTILITY FUNCTIONS
// ═══════════════════════════════════════════════════════════════

/**
 * Escape a string for safe insertion into an HTML attribute (value="...").
 * Prevents XSS if profile data contains quotes or angle brackets.
 */
function escapeAttr(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

/**
 * Escape a string for safe insertion into HTML content.
 */
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}


// ═══════════════════════════════════════════════════════════════
// 8. EVENT WIRING — connect buttons to functions
// ═══════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
    // ── Load profile on page load ────────────────────────────
    loadProfile();

    // ── Save button ──────────────────────────────────────────
    saveBtn.addEventListener("click", saveProfile);

    // ── "Add" buttons ────────────────────────────────────────
    document.getElementById("add-link-btn").addEventListener("click", () => addLinkRow());
    document.getElementById("add-education-btn").addEventListener("click", () => addEducationItem());
    document.getElementById("add-experience-btn").addEventListener("click", () => addExperienceItem());
    document.getElementById("add-project-btn").addEventListener("click", () => addProjectItem());
    document.getElementById("add-certification-btn").addEventListener("click", () => addCertificationItem());
    document.getElementById("add-skill-category-btn").addEventListener("click", () => addSkillCategory());

    // ── Keyboard shortcut: Ctrl+S to save ────────────────────
    document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {
            e.preventDefault();
            saveProfile();
        }
    });
});
