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

// Import TipTap editor via esm.sh CDN (solves duplicate ProseMirror dependency issues)
import { Editor } from 'https://esm.sh/@tiptap/core@2.2.2';
import StarterKit from 'https://esm.sh/@tiptap/starter-kit@2.2.2';

// Store TipTap editor instances
const editorInstances = new Map();
let achievementsEditorId = null;

// Helper to create and initialize TipTap rich editors
function createRichEditor(parentElement, initialHtml, placeholder, showList = false) {
    const editorId = "editor-" + Math.random().toString(36).substring(2, 9);
    
    const wrapper = document.createElement("div");
    wrapper.className = "rich-editor-wrapper";
    
    let toolbarHtml = `
        <div class="rich-editor-toolbar">
            <button type="button" class="toolbar-btn bold-btn" title="Bold (Ctrl+B)"><i data-lucide="bold"></i></button>
            <button type="button" class="toolbar-btn italic-btn" title="Italic (Ctrl+I)"><i data-lucide="italic"></i></button>
    `;
    if (showList) {
        toolbarHtml += `
            <div class="toolbar-dropdown">
                <button type="button" class="toolbar-btn dropdown-trigger" title="Lists">
                    List <span class="arrow-down">▼</span>
                </button>
                <div class="dropdown-menu">
                    <button type="button" class="dropdown-item bullet-list-btn" title="Bullet List">
                        <span class="check-indicator"></span>Bullet List
                    </button>
                    <button type="button" class="dropdown-item ordered-list-btn" title="Numbered List">
                        <span class="check-indicator"></span>Numbered List
                    </button>
                </div>
            </div>
        `;
    }
    toolbarHtml += `</div>`;
    
    wrapper.innerHTML = `
        ${toolbarHtml}
        <div class="rich-editor-content" data-editor-id="${editorId}"></div>
    `;
    
    parentElement.appendChild(wrapper);
    
    const contentEl = wrapper.querySelector(".rich-editor-content");
    const boldBtn = wrapper.querySelector(".bold-btn");
    const italicBtn = wrapper.querySelector(".italic-btn");
    
    const editor = new Editor({
        element: contentEl,
        extensions: [StarterKit],
        content: sanitizeRichHtml(initialHtml) || "",
    });
    
    // Set placeholder on content area if empty
    if (placeholder) {
        contentEl.querySelector(".ProseMirror").setAttribute("data-placeholder", placeholder);
    }
    
    editorInstances.set(editorId, editor);
    
    // Wire bold & italic buttons
    boldBtn.addEventListener("click", (e) => {
        e.preventDefault();
        editor.chain().focus().toggleBold().run();
    });
    
    italicBtn.addEventListener("click", (e) => {
        e.preventDefault();
        editor.chain().focus().toggleItalic().run();
    });
    
    if (showList) {
        const trigger = wrapper.querySelector(".dropdown-trigger");
        const menu = wrapper.querySelector(".dropdown-menu");
        const bulletListBtn = wrapper.querySelector(".bullet-list-btn");
        const orderedListBtn = wrapper.querySelector(".ordered-list-btn");
        
        trigger.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            menu.classList.toggle("visible");
        });
        
        bulletListBtn.addEventListener("click", (e) => {
            e.preventDefault();
            editor.chain().focus().toggleBulletList().run();
            menu.classList.remove("visible");
        });
        
        orderedListBtn.addEventListener("click", (e) => {
            e.preventDefault();
            editor.chain().focus().toggleOrderedList().run();
            menu.classList.remove("visible");
        });
        
        document.addEventListener("click", (e) => {
            if (!wrapper.contains(e.target)) {
                menu.classList.remove("visible");
            }
        });
        
        // Update list states inside toolbar active handlers
        editor.on("transaction", () => {
            boldBtn.classList.toggle("active", editor.isActive("bold"));
            italicBtn.classList.toggle("active", editor.isActive("italic"));
            
            const isBulletActive = editor.isActive("bulletList");
            const isOrderedActive = editor.isActive("orderedList");
            
            const bulletCheck = bulletListBtn.querySelector(".check-indicator");
            const orderedCheck = orderedListBtn.querySelector(".check-indicator");
            if (bulletCheck) bulletCheck.textContent = isBulletActive ? "✓" : "";
            if (orderedCheck) orderedCheck.textContent = isOrderedActive ? "✓" : "";
            
            bulletListBtn.classList.toggle("active", isBulletActive);
            orderedListBtn.classList.toggle("active", isOrderedActive);
            
            trigger.classList.toggle("active", isBulletActive || isOrderedActive);
        });
    } else {
        editor.on("transaction", () => {
            boldBtn.classList.toggle("active", editor.isActive("bold"));
            italicBtn.classList.toggle("active", editor.isActive("italic"));
        });
    }
    
    refreshIcons();
    return editorId;
}

// Helper to destroy editors inside a removed list item
function destroyEditorsIn(element) {
    element.querySelectorAll("[data-editor-id]").forEach(el => {
        const id = el.dataset.editorId;
        const editor = editorInstances.get(id);
        if (editor) {
            editor.destroy();
            editorInstances.delete(id);
        }
    });
}

// Helper to parse bullet list items from TipTap editor HTML
function parseBulletsFromHtml(html) {
    if (!html || html === "<p></p>" || html === "<p><br></p>") return [];
    
    const temp = document.createElement("div");
    temp.innerHTML = html;
    
    // Try to find list items (li)
    const lis = temp.querySelectorAll("li");
    if (lis.length > 0) {
        return Array.from(lis).map(li => li.innerHTML.trim()).filter(Boolean);
    }
    
    // Try to find paragraphs (p)
    const paragraphs = temp.querySelectorAll("p");
    if (paragraphs.length > 0) {
        return Array.from(paragraphs).map(p => p.innerHTML.trim()).filter(Boolean);
    }
    
    const cleanHTML = temp.innerHTML.trim();
    if (cleanHTML && cleanHTML !== "<br>") {
        return [cleanHTML];
    }
    
    return [];
}

// ── DOM references ──────────────────────────────────────────
const saveBtn         = document.getElementById("save-btn");
const saveBtnText     = document.getElementById("save-btn-text");
const generateBtn     = document.getElementById("generate-btn");
const generateBtnText = document.getElementById("generate-btn-text");
const errorSummary    = document.getElementById("error-summary");
const errorList       = document.getElementById("error-list");
const toastContainer  = document.getElementById("toast-container");

// Upload & Modal references
const uploadContainer = document.getElementById("upload-container");
const browseBtn       = document.getElementById("browse-btn");
const fileInput       = document.getElementById("resume-file-input");

const confirmationModal = document.getElementById("confirmation-modal");
const confirmCloseBtn   = document.getElementById("confirm-modal-close");
const confirmCancelBtn  = document.getElementById("confirm-modal-cancel");
const confirmConfirmBtn = document.getElementById("confirm-modal-confirm");
const summaryList       = document.getElementById("extraction-summary-list");

const loadingOverlay   = document.getElementById("loading-overlay");
const loadingMessage   = document.getElementById("loading-message");

// List containers
const educationList     = document.getElementById("education-list");
const experienceList    = document.getElementById("experience-list");
const projectList       = document.getElementById("project-list");
const certificationList = document.getElementById("certification-list");
const skillsList        = document.getElementById("skills-list");

// Resume library
const libraryList       = document.getElementById("resume-library-list");
const libraryEmptyState = document.getElementById("library-empty-state");
const libraryNoResults  = document.getElementById("library-no-results");


// ── Icons Helper ────────────────────────────────────────────
function refreshIcons() {
    if (typeof lucide !== "undefined") {
        lucide.createIcons();
    }
}


// ═══════════════════════════════════════════════════════════════
// 1. LOAD PROFILE — fetch from API and populate the form
// ═══════════════════════════════════════════════════════════════

let hasValidMasterResume = false;
let cachedProfile = null;

// ── Simple SPA Router ───────────────────────────────────────
function router() {
    const hash = window.location.hash || "#home";
    const views = ["#view-home", "#view-builder", "#view-history", "#view-tailor", "#view-tailor-workspace"];
    
    views.forEach(vId => {
        const el = document.getElementById(vId.substring(1));
        if (el) {
            el.classList.remove("active");
        }
    });

    const activeViewId = hash.substring(1);
    const activeView = document.getElementById(`view-${activeViewId}`);
    if (activeView) {
        activeView.classList.add("active");
    } else {
        const homeView = document.getElementById("view-home");
        if (homeView) homeView.classList.add("active");
    }

    // Toggle save bar & body state
    if (hash === "#builder") {
        document.body.classList.add("builder-active");
    } else {
        document.body.classList.remove("builder-active");
    }

    if (hash === "#tailor-workspace") {
        document.body.classList.add("workspace-active");
        initializeTailoringWorkspace();
    } else {
        document.body.classList.remove("workspace-active");
    }

    // Refresh the resume library whenever the History page is opened, so it
    // reflects anything generated elsewhere since the last full page load.
    if (hash === "#history") {
        loadLibrary();
    }

    // Load setup data if tailoring page is opened
    if (hash === "#tailor") {
        // Always autofill contact details from the master resume.
        // This is the SOURCE OF TRUTH for name / email / phone.
        // We only fill empty fields so the user can override per-application
        // without losing their edits.
        const autofillContactFromMaster = () => {
            const pi = (cachedProfile && cachedProfile.personal_info) || {};
            const nameField = document.getElementById("tailor-pi-name");
            const emailField = document.getElementById("tailor-pi-email");
            const phoneField = document.getElementById("tailor-pi-phone");
            if (nameField && !nameField.value.trim() && pi.name) nameField.value = pi.name;
            if (emailField && !emailField.value.trim() && pi.email) emailField.value = pi.email;
            if (phoneField && !phoneField.value.trim() && (pi.phone || ""))
                phoneField.value = pi.phone || "";
        };

        if (cachedProfile) {
            autofillContactFromMaster();
        } else {
            // Race-safe: the initial DOMContentLoaded loadProfile() fetch
            // may still be in flight when the user navigates here. Kick
            // off a fetch in the background and autofill as soon as it
            // arrives.
            fetch("/api/profile")
                .then(r => r.ok ? r.json() : null)
                .then(profile => {
                    if (!profile || profile.error) return;
                    cachedProfile = profile;
                    hasValidMasterResume = !!profile.has_valid_resume;
                    autofillContactFromMaster();
                })
                .catch(() => { /* silent: user can fill manually */ });
        }

        // Restore setup form state from sessionStorage
        try {
            const savedState = sessionStorage.getItem("tailorSetupState");
            if (savedState) {
                const state = JSON.parse(savedState);
                if (state.job_description) {
                    const jdText = document.getElementById("tailor-jd-text");
                    if (jdText && !jdText.value.trim()) {
                        jdText.value = state.job_description;
                        updateJdStatus();
                    }
                }
                if (state.job_url) {
                    const jdUrl = document.getElementById("tailor-jd-url");
                    if (jdUrl && !jdUrl.value.trim()) jdUrl.value = state.job_url;
                }
                // Only fall back to sessionStorage contact info if the
                // field is still empty AFTER the master-resume autofill.
                // This prevents a previously saved empty contact_info from
                // wiping the master values on subsequent visits.
                if (state.contact_info) {
                    const c = state.contact_info;
                    const nameField = document.getElementById("tailor-pi-name");
                    const emailField = document.getElementById("tailor-pi-email");
                    const phoneField = document.getElementById("tailor-pi-phone");
                    if (nameField && !nameField.value.trim() && c.name) nameField.value = c.name;
                    if (emailField && !emailField.value.trim() && c.email) emailField.value = c.email;
                    if (phoneField && !phoneField.value.trim() && c.phone) phoneField.value = c.phone;
                }
                if (state.preferences) {
                    const p = state.preferences;
                    if (p.style) {
                        const rad = document.getElementById(`style-${p.style}`);
                        if (rad) {
                            rad.checked = true;
                            document.querySelectorAll("input[name='tailor-style']").forEach(input => {
                                const card = input.closest(".style-radio-card");
                                if (card) card.classList.toggle("active", input.checked);
                            });
                            // Dispatch change event to synchronize the range slider UI
                            rad.dispatchEvent(new Event("change"));
                        }
                    }
                    if (p.job_level) {
                        const levelSel = document.getElementById("tailor-job-level");
                        if (levelSel) levelSel.value = p.job_level;
                    }
                    if (p.focus_areas && Array.isArray(p.focus_areas)) {
                        ["skills", "projects", "experience", "summary"].forEach(area => {
                            const cb = document.getElementById(`focus-${area}`);
                            if (cb) cb.checked = p.focus_areas.includes(area);
                        });
                    }
                }
            }
        } catch (e) {
            console.error("Error loading saved tailoring state:", e);
        }
        
        updateTailorChecklist();
    }

    refreshIcons();
    window.scrollTo({ top: 0, behavior: "instant" });
}

// ── Update the "Master Resume Available" tag on the home page ─
function updateHomeMasterTag(profile) {
    const homeMasterTag = document.getElementById("home-master-tag");
    if (!homeMasterTag) return;
    homeMasterTag.style.display = profile.has_valid_resume ? "inline-flex" : "none";
}

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

        // Store validation status globally for routing checks
        hasValidMasterResume = !!profile.has_valid_resume;
        cachedProfile = profile;

        updateHomeMasterTag(profile);
        populateForm(profile);
        refreshIcons();
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

    // ── Achievements ─────────────────────────────────────────
    const achievementsContainer = document.getElementById("achievements-editor-container");
    if (achievementsContainer) {
        achievementsContainer.innerHTML = "";
        const achievements = profile.achievements || "";
        let initialHtml = "";
        if (typeof achievements === "string") {
            initialHtml = achievements;
        } else if (Array.isArray(achievements)) {
            if (achievements.length > 0) {
                initialHtml = `<ul>${achievements.map(ach => `<li>${ach?.title || ach}</li>`).join("")}</ul>`;
            }
        }
        achievementsEditorId = createRichEditor(
            achievementsContainer,
            initialHtml,
            "Describe your achievements (e.g. Won first place in ACM ICPC Regional 2024)...",
            true
        );
    }
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
        <button type="button" class="btn-remove" title="Remove link"><i data-lucide="x"></i></button>
    `;
    row.querySelector(".btn-remove").addEventListener("click", () => {
        row.remove();
    });
    container.appendChild(row);
    refreshIcons();
}


// ── Education ────────────────────────────────────────────────

function addEducationItem(data) {
    const item = document.createElement("div");
    item.className = "list-item education-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove"><i data-lucide="trash-2"></i> Remove</button>
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
    refreshIcons();
}


// ── Experience ───────────────────────────────────────────────

function addExperienceItem(data) {
    const item = document.createElement("div");
    item.className = "list-item experience-item";

    // Build work mode dropdown options
    const workModeVal = data?.work_mode || "";
    const workModeOptions = ["", "Onsite", "Remote", "Hybrid"];
    const workModeOptionsHtml = workModeOptions.map(opt => {
        const selected = opt === workModeVal ? "selected" : "";
        const label = opt || "— Select —";
        return `<option value="${escapeAttr(opt)}" ${selected}>${escapeHtml(label)}</option>`;
    }).join("");

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove"><i data-lucide="trash-2"></i> Remove</button>
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
            <div class="form-group">
                <label>Work Mode</label>
                <select class="exp-work-mode">${workModeOptionsHtml}</select>
            </div>
            <div class="form-group full-width">
                <label>Description / Bullet Points</label>
                <div class="exp-bullets-editor-container exp-bullets"></div>
                <span class="field-error"></span>
            </div>
            <div class="form-group full-width">
                <label>Technologies (comma-separated)</label>
                <input type="text" class="exp-technologies" placeholder="e.g. Python, Flask, PostgreSQL, Docker" value="${escapeAttr((data?.technologies || []).join(", "))}">
            </div>
        </div>
    `;

    // Initialize TipTap rich editor for bullets (supporting ordered/numbered and unordered lists)
    const editorContainer = item.querySelector(".exp-bullets-editor-container");
    let initialHtml = "";
    const listType = data?.list_type || "bullet";
    const isOrdered = listType === "numbered";
    const bullets = data?.bullets || [];
    
    if (bullets.length > 0) {
        const listTag = isOrdered ? "ol" : "ul";
        initialHtml = `<${listTag}>${bullets.map(b => `<li>${b}</li>`).join("")}</${listTag}>`;
    } else {
        initialHtml = "<ul><li></li></ul>";
    }
    const editorId = createRichEditor(editorContainer, initialHtml, "Describe your achievements and responsibilities...", true);
    item.dataset.bulletsEditorId = editorId;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        destroyEditorsIn(item);
        item.remove();
        renumberItems(experienceList, "Experience");
    });

    experienceList.appendChild(item);
    renumberItems(experienceList, "Experience");
    refreshIcons();
}


// ── Projects ─────────────────────────────────────────────────

function addProjectItem(data) {
    const item = document.createElement("div");
    item.className = "list-item project-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove"><i data-lucide="trash-2"></i> Remove</button>
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
                <div class="proj-description-editor-container proj-description"></div>
                <span class="field-error"></span>
            </div>
            <div class="form-group full-width">
                <label>Key Highlights</label>
                <div class="proj-bullets-editor-container proj-bullets"></div>
                <span class="field-error"></span>
            </div>
            <div class="form-group full-width">
                <label>Technologies (comma-separated)</label>
                <input type="text" class="proj-technologies" placeholder="e.g. React, Node.js, MongoDB" value="${escapeAttr((data?.technologies || []).join(", "))}">
            </div>
        </div>
    `;

    // Initialize TipTap rich editor for description (no list)
    const descContainer = item.querySelector(".proj-description-editor-container");
    const descEditorId = createRichEditor(descContainer, data?.description || "", "One-line summary of the project...", false);
    item.dataset.descEditorId = descEditorId;

    // Initialize TipTap rich editor for bullets (supporting ordered/numbered and unordered lists)
    const bulletsContainer = item.querySelector(".proj-bullets-editor-container");
    let projBulletsHtml = "";
    const listType = data?.list_type || "bullet";
    const isProjOrdered = listType === "numbered";
    const bullets = data?.bullets || [];
    
    if (bullets.length > 0) {
        const listTag = isProjOrdered ? "ol" : "ul";
        projBulletsHtml = `<${listTag}>${bullets.map(b => `<li>${b}</li>`).join("")}</${listTag}>`;
    } else {
        projBulletsHtml = "<ul><li></li></ul>";
    }
    const bulletsEditorId = createRichEditor(bulletsContainer, projBulletsHtml, "Key accomplishments / technical highlights...", true);
    item.dataset.bulletsEditorId = bulletsEditorId;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        destroyEditorsIn(item);
        item.remove();
        renumberItems(projectList, "Project");
    });

    projectList.appendChild(item);
    renumberItems(projectList, "Project");
    refreshIcons();
}


// ── Certifications ───────────────────────────────────────────

function addCertificationItem(data) {
    const item = document.createElement("div");
    item.className = "list-item certification-item";

    item.innerHTML = `
        <div class="list-item-header">
            <span class="list-item-number"></span>
            <button type="button" class="btn-remove"><i data-lucide="trash-2"></i> Remove</button>
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
            <div class="form-group full-width">
                <label>Description (optional)</label>
                <div class="cert-description-editor-container cert-description"></div>
                <span class="field-error"></span>
            </div>
        </div>
    `;

    // Initialize TipTap rich editor for description (no list)
    const container = item.querySelector(".cert-description-editor-container");
    const editorId = createRichEditor(container, data?.description || "", "Describe details of the certification...", false);
    item.dataset.editorId = editorId;

    item.querySelector(".btn-remove").addEventListener("click", () => {
        destroyEditorsIn(item);
        item.remove();
        renumberItems(certificationList, "Certification");
    });

    certificationList.appendChild(item);
    renumberItems(certificationList, "Certification");
    refreshIcons();
}





// ── Skills ───────────────────────────────────────────────────

function addSkillCategory(catName, skills) {
    const cat = document.createElement("div");
    cat.className = "skill-category";

    cat.innerHTML = `
        <div class="skill-category-header">
            <input type="text" class="skill-cat-name" placeholder="Category name (e.g. Languages)" value="${escapeAttr(catName || "")}">
            <button type="button" class="btn-remove" title="Remove category"><i data-lucide="x"></i></button>
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
            <button type="button" class="skill-tag-remove" title="Remove"><i data-lucide="x"></i></button>
        `;
        tag.querySelector(".skill-tag-remove").addEventListener("click", () => {
            tag.remove();
        });
        tagsContainer.appendChild(tag);
        refreshIcons();
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
    refreshIcons();
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
        const techTxt = item.querySelector(".exp-technologies").value.trim();
        const workMode = item.querySelector(".exp-work-mode").value || null;
        
        // Extract rich text from TipTap editor
        const editorId = item.dataset.bulletsEditorId;
        const editor = editorInstances.get(editorId);
        const editorHtml = editor ? editor.getHTML() : "";
        const bullets = parseBulletsFromHtml(editorHtml);
        const isNumbered = editor ? editor.isActive("orderedList") : editorHtml.includes("<ol>");
        const listType = isNumbered ? "numbered" : "bullet";

        profile.experience.push({
            company:      item.querySelector(".exp-company").value,
            role:         item.querySelector(".exp-role").value,
            start_date:   item.querySelector(".exp-start-date").value,
            end_date:     item.querySelector(".exp-end-date").value,
            work_mode:    workMode,
            bullets:      bullets,
            list_type:    listType,
            technologies: techTxt ? techTxt.split(",").map(s => s.trim()).filter(Boolean) : [],
        });
    });

    // ── Projects ─────────────────────────────────────────────
    profile.projects = [];
    projectList.querySelectorAll(".project-item").forEach(item => {
        const techTxt = item.querySelector(".proj-technologies").value.trim();
        const linkVal = item.querySelector(".proj-link").value.trim();

        // Extract description from TipTap
        const descEditorId = item.dataset.descEditorId;
        const descEditor = editorInstances.get(descEditorId);
        const description = descEditor ? descEditor.getHTML() : "";

        // Extract bullets from TipTap
        const bulletsEditorId = item.dataset.bulletsEditorId;
        const bulletsEditor = editorInstances.get(bulletsEditorId);
        const bulletsHtml = bulletsEditor ? bulletsEditor.getHTML() : "";
        const bullets = parseBulletsFromHtml(bulletsHtml);
        const isProjNumbered = bulletsEditor ? bulletsEditor.isActive("orderedList") : bulletsHtml.includes("<ol>");
        const listType = isProjNumbered ? "numbered" : "bullet";

        profile.projects.push({
            name:         item.querySelector(".proj-name").value,
            description:  description,
            bullets:      bullets,
            list_type:    listType,
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
        const editorId = item.dataset.editorId;
        const editor = editorInstances.get(editorId);
        const description = editor ? editor.getHTML() : "";

        profile.certifications.push({
            name:        item.querySelector(".cert-name").value,
            issuer:      item.querySelector(".cert-issuer").value,
            date:        item.querySelector(".cert-date").value,
            description: description,
        });
    });

    // ── Achievements ─────────────────────────────────────────
    const achievementsEditor = editorInstances.get(achievementsEditorId);
    profile.achievements = achievementsEditor ? achievementsEditor.getHTML() : "";

    return profile;
}


// ═══════════════════════════════════════════════════════════════
// 4. SAVE PROFILE — collect data and PUT to API
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// 4. SAVE MASTER RESUME — collect data and PUT to API
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
        const response = await fetch("/api/profile", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });

        const result = await response.json();

        if (response.ok) {
            showToast("Master Resume Saved Successfully", "success");
            await loadProfile();
            // User requested to remain on the page, so no redirect.
        } else if (response.status === 422 && result.errors) {
            displayErrors(result.errors);
            showToast("Please fix the validation errors below.", "error");
        } else {
            showToast(result.error || "Something went wrong.", "error");
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, "error");
    } finally {
        saveBtn.disabled = false;
        saveBtnText.textContent = "Save Master Resume";
    }
}

// Helper to save silently in the background before downloading
async function saveProfileSilent() {
    clearErrors();
    const data = collectFormData();
    try {
        const response = await fetch("/api/profile", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        const result = await response.json();
        if (response.ok) {
            await loadProfile();
            return true;
        } else if (response.status === 422 && result.errors) {
            displayErrors(result.errors);
            showToast("Please fix the validation errors before downloading.", "error");
            return false;
        } else {
            showToast(result.error || "Failed to auto-save before download.", "error");
            return false;
        }
    } catch (err) {
        showToast(`Auto-save network error: ${err.message}`, "error");
        return false;
    }
}

// ═══════════════════════════════════════════════════════════════
// 4b. DOWNLOAD MASTER RESUME PDF — POST to /api/resume/master
// ═══════════════════════════════════════════════════════════════

async function generateResume() {
    generateBtn.disabled = true;
    generateBtnText.textContent = "Saving...";

    // 1. Silent Save
    const saveSuccess = await saveProfileSilent();
    if (!saveSuccess) {
        generateBtn.disabled = false;
        generateBtnText.textContent = "Download Master Resume PDF";
        refreshIcons();
        return;
    }

    // 2. Generate and Download
    generateBtnText.textContent = "Generating...";
    try {
        const response = await fetch("/api/resume/master", {
            method: "POST",
        });

        const result = await response.json();

        if (response.ok && result.status === "ok") {
            showToast("Master Resume PDF downloaded successfully!", "success");

            // Refresh the library list
            loadLibrary();

            // Trigger file download
            downloadFile(
                `/api/resumes/${encodeURIComponent(result.id)}/pdf`,
                `${result.id}.pdf`
            );
        } else {
            const errorMsg = result.error || "Failed to generate resume.";
            showToast(errorMsg, "error");

            if (result.log) {
                console.error("Tectonic compilation log:\n", result.log);
            }
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, "error");
    } finally {
        generateBtn.disabled = false;
        generateBtnText.textContent = "Download Master Resume PDF";
        refreshIcons();
    }
}

// ═══════════════════════════════════════════════════════════════
// 4c. UPLOAD EXISTING RESUME — upload & parse via AI
// ═══════════════════════════════════════════════════════════════

let pendingExtractedProfile = null;

// Trigger file input dialog
if (browseBtn && fileInput) {
    browseBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        fileInput.click();
    });
}

// Drag and drop events on container
if (uploadContainer && fileInput) {
    uploadContainer.addEventListener("click", (e) => {
        if (e.target !== browseBtn) {
            fileInput.click();
        }
    });

    ["dragenter", "dragover"].forEach(eventName => {
        uploadContainer.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            uploadContainer.classList.add("dragover");
        }, false);
    });

    ["dragleave", "drop"].forEach(eventName => {
        uploadContainer.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            uploadContainer.classList.remove("dragover");
        }, false);
    });

    uploadContainer.addEventListener("drop", (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files && files.length > 0) {
            handleUploadedFile(files[0]);
        }
    }, false);
}

if (fileInput) {
    fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
            handleUploadedFile(e.target.files[0]);
        }
    });
}

async function handleUploadedFile(file) {
    const filename = file.name.toLowerCase();
    if (!filename.endsWith(".pdf") && !filename.endsWith(".docx")) {
        showToast("Unsupported file type. Please upload a PDF or DOCX file.", "error");
        return;
    }

    loadingOverlay.classList.add("active");
    loadingMessage.textContent = "Uploading Resume...";

    let phaseTimer1 = setTimeout(() => {
        loadingMessage.textContent = "Extracting Text...";
    }, 1200);

    let phaseTimer2 = setTimeout(() => {
        loadingMessage.textContent = "AI Understanding Resume...";
    }, 2500);

    const formData = new FormData();
    formData.append("file", file);

    try {
        const response = await fetch("/api/profile/parse", {
            method: "POST",
            body: formData
        });

        clearTimeout(phaseTimer1);
        clearTimeout(phaseTimer2);

        if (!response.ok) {
            const result = await response.json();
            throw new Error(result.error || "Failed to parse resume.");
        }

        const profile = await response.json();
        
        loadingMessage.textContent = "Populating Master Resume...";
        setTimeout(() => {
            loadingOverlay.classList.remove("active");
            showConfirmationModal(profile);
        }, 600);

    } catch (err) {
        clearTimeout(phaseTimer1);
        clearTimeout(phaseTimer2);
        loadingOverlay.classList.remove("active");
        showToast(err.message, "error");
        if (fileInput) fileInput.value = "";
    }
}

function showConfirmationModal(profile) {
    pendingExtractedProfile = profile;
    summaryList.innerHTML = "";

    const name = profile.personal_info?.name || "N/A";
    const eduCount = profile.education?.length || 0;
    const expCount = profile.experience?.length || 0;
    const projCount = profile.projects?.length || 0;
    
    let skillsCount = 0;
    if (profile.skills?.categories) {
        skillsCount = Object.keys(profile.skills.categories).length;
    }
    const certCount = profile.certifications?.length || 0;

    const summaryItems = [
        { label: "Name", value: name },
        { label: "Education Credentials", value: eduCount },
        { label: "Work Experience Entries", value: expCount },
        { label: "Projects", value: projCount },
        { label: "Skill Categories", value: skillsCount },
        { label: "Certifications", value: certCount }
    ];

    summaryItems.forEach(item => {
        const li = document.createElement("li");
        li.innerHTML = `<span>${escapeHtml(item.label)}</span> <span class="item-count">${escapeHtml(String(item.value))}</span>`;
        summaryList.appendChild(li);
    });

    confirmationModal.classList.add("active");
}

function closeModal() {
    confirmationModal.classList.remove("active");
    pendingExtractedProfile = null;
    if (fileInput) fileInput.value = "";
}

if (confirmCloseBtn) confirmCloseBtn.addEventListener("click", closeModal);
if (confirmCancelBtn) confirmCancelBtn.addEventListener("click", closeModal);
if (confirmConfirmBtn) {
    confirmConfirmBtn.addEventListener("click", () => {
        if (pendingExtractedProfile) {
            populateForm(pendingExtractedProfile);
            showToast("Master Resume imported successfully! All fields remain fully editable.", "success");
        }
        closeModal();
    });
}

// ═══════════════════════════════════════════════════════════════
// 4d. RESUME LIBRARY — browse, download, and delete saved resumes
// ═══════════════════════════════════════════════════════════════
//
// The library fetches GET /api/resumes on load and after each
// generate/delete operation.  Each resume shows its label, type
// badge, date, and action buttons (download PDF, download .tex,
// delete).
// ═══════════════════════════════════════════════════════════════

// Cached full list from the last successful fetch, so search/filter can
// re-render instantly without round-tripping to the server on every
// keystroke or filter-button click.
let _libraryResumes = [];
let _libraryFilterType = "all";
let _librarySearchQuery = "";

async function loadLibrary() {
    try {
        const response = await fetch("/api/resumes");
        const resumes = await response.json();

        // Handle error response
        if (resumes.error) {
            console.error("Failed to load library:", resumes.error);
            return;
        }

        _libraryResumes = resumes;
        renderLibraryList();
        renderHomeHistoryPreview();
    } catch (err) {
        console.error("Failed to load resume library:", err);
    }
}

// ── Home page History preview (last 5 generations) ────────────
// Reuses renderLibraryItem() so the preview cards on the home page get the
// exact same download/rename/duplicate/delete actions as the full Resume
// Library page — one code path, two mount points, always in sync because
// both are driven from the same loadLibrary() fetch.
function renderHomeHistoryPreview() {
    const list = document.getElementById("home-history-list");
    const emptyState = document.getElementById("home-history-empty");
    if (!list || !emptyState) return;

    list.innerHTML = "";

    if (_libraryResumes.length === 0) {
        emptyState.style.display = "block";
        return;
    }
    emptyState.style.display = "none";

    _libraryResumes.slice(0, 5).forEach(resume => {
        list.appendChild(renderLibraryItem(resume));
    });

    refreshIcons();
}

function renderLibraryList() {
    libraryList.innerHTML = "";

    if (_libraryResumes.length === 0) {
        libraryEmptyState.style.display = "block";
        libraryNoResults.style.display = "none";
        return;
    }
    libraryEmptyState.style.display = "none";

    const query = _librarySearchQuery.trim().toLowerCase();
    const filtered = _libraryResumes.filter(resume => {
        const matchesType = _libraryFilterType === "all" || resume.type === _libraryFilterType;
        const matchesQuery = !query || (resume.label || "").toLowerCase().includes(query);
        return matchesType && matchesQuery;
    });

    if (filtered.length === 0) {
        libraryNoResults.style.display = "block";
        refreshIcons();
        return;
    }
    libraryNoResults.style.display = "none";

    filtered.forEach(resume => {
        libraryList.appendChild(renderLibraryItem(resume));
    });

    refreshIcons();
}

function renderLibraryItem(resume) {
    const item = document.createElement("div");
    item.className = "library-item";
    item.dataset.resumeId = resume.id;

    // Format the date nicely
    const dateStr = resume.date
        ? new Date(resume.date).toLocaleDateString("en-US", {
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })
        : "Unknown date";

    // Badge class based on type
    const badgeClass = resume.type === "master"
        ? "library-badge library-badge-master"
        : "library-badge library-badge-tailored";
    const badgeText = resume.type === "master" ? "Master" : "Tailored";

    item.innerHTML = `
        <div class="library-item-info">
            <span class="library-item-label">${escapeHtml(resume.label)}</span>
            <div class="library-item-meta">
                <span class="${badgeClass}">${badgeText}</span>
                <span>${escapeHtml(dateStr)}</span>
            </div>
        </div>
        <div class="library-item-actions">
            ${resume.has_pdf ? `
                <button type="button" class="btn-icon-action btn-download-pdf" title="Download PDF">
                    <i data-lucide="file-text"></i>
                </button>
            ` : ""}
            ${resume.has_tex ? `
                <button type="button" class="btn-icon-action btn-download-tex" title="Download .tex">
                    <i data-lucide="file-code"></i>
                </button>
            ` : ""}
            <button type="button" class="btn-icon-action btn-rename" title="Rename">
                <i data-lucide="pencil"></i>
            </button>
            <button type="button" class="btn-icon-action btn-duplicate" title="Duplicate">
                <i data-lucide="copy"></i>
            </button>
            <button type="button" class="btn-icon-action btn-delete" title="Delete resume">
                <i data-lucide="trash-2"></i>
            </button>
        </div>
    `;

    // ── Wire up action buttons ───────────────────────────────
    const pdfBtn = item.querySelector(".btn-download-pdf");
    if (pdfBtn) {
        pdfBtn.addEventListener("click", () => {
            downloadFile(
                `/api/resumes/${encodeURIComponent(resume.id)}/pdf`,
                `${resume.id}.pdf`
            );
        });
    }

    const texBtn = item.querySelector(".btn-download-tex");
    if (texBtn) {
        texBtn.addEventListener("click", () => {
            downloadFile(
                `/api/resumes/${encodeURIComponent(resume.id)}/tex`,
                `${resume.id}.tex`
            );
        });
    }

    item.querySelector(".btn-rename").addEventListener("click", () => {
        renameResumeInLibrary(resume);
    });

    item.querySelector(".btn-duplicate").addEventListener("click", () => {
        duplicateResumeInLibrary(resume);
    });

    const deleteBtn = item.querySelector(".btn-delete");
    deleteBtn.addEventListener("click", () => {
        deleteResumeFromLibrary(resume.id, item);
    });

    return item;
}

async function renameResumeInLibrary(resume) {
    const newLabel = await showRenameModal({
        title: "Rename Resume",
        initialValue: resume.label,
    });
    if (!newLabel || newLabel === resume.label) return;

    try {
        const response = await fetch(`/api/resumes/${encodeURIComponent(resume.id)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label: newLabel }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Failed to rename resume.");

        showToast("Resume renamed.", "success");
        await loadLibrary();
    } catch (err) {
        showToast(err.message, "error");
    }
}

async function duplicateResumeInLibrary(resume) {
    try {
        const response = await fetch(`/api/resumes/${encodeURIComponent(resume.id)}/duplicate`, {
            method: "POST",
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Failed to duplicate resume.");

        showToast("Resume duplicated.", "success");
        await loadLibrary();
    } catch (err) {
        showToast(err.message, "error");
    }
}

async function deleteResumeFromLibrary(resumeId, itemElement) {
    // ── Confirm before deleting ──────────────────────────────
    const confirmed = await showConfirmModal({
        title: "Delete Resume",
        message: "Delete this resume? This cannot be undone.",
        confirmText: "Delete",
    });
    if (!confirmed) {
        return;
    }

    try {
        const response = await fetch(
            `/api/resumes/${encodeURIComponent(resumeId)}`,
            { method: "DELETE" }
        );

        const result = await response.json();

        if (response.ok && result.status === "ok") {
            // ── Animate removal ──────────────────────────────
            itemElement.classList.add("removing");
            setTimeout(() => {
                itemElement.remove();
                // Show empty state if no items left
                if (libraryList.children.length === 0) {
                    libraryEmptyState.style.display = "block";
                }
            }, 400);

            showToast("Resume deleted.", "success");
        } else {
            showToast(result.error || "Failed to delete resume.", "error");
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, "error");
    }
}

function downloadFile(url, filename) {
    // Trigger a file download by creating a temporary link.
    // This works for same-origin URLs served by our Flask backend.
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
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
    if (!loc || loc.length === 0) return null;

    const section = loc[0];
    if (section === "achievements") {
        return document.getElementById("achievements-editor-container") || null;
    }

    if (loc.length < 2) return null;

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
            bullets: "bullets-editor-container", technologies: "technologies",
            // Projects
            name: "name", description: "description-editor-container",
            bullets: "bullets-editor-container", link: "link",
            // Certifications
            issuer: "issuer", date: "date", description: "description-editor-container",
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
        <i data-lucide="${type === 'success' ? 'check-circle' : 'alert-circle'}" class="toast-icon"></i>
        <span>${escapeHtml(message)}</span>
    `;
    toastContainer.appendChild(toast);
    refreshIcons();

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


// ── Rich-text HTML sanitizer ──────────────────────────────────
// Bullets, project/certification descriptions, and achievements are
// stored as small HTML fragments (bold/italic/lists) produced by the
// TipTap editor or the AI tailoring pipeline. Several places in this
// file inject that stored HTML directly via innerHTML (both to seed
// TipTap's `content:` option and, in the AI-tailoring workspace editor,
// straight into a plain contenteditable element). If profile.json is
// ever hand-edited, restored from an untrusted source, or a future
// parsing edge case lands something unexpected in a bullet, this
// prevents it from executing as script/event-handler markup.
//
// Parsing happens inside a detached <template> element: per spec its
// .content is an inert DocumentFragment, so nothing (image loads,
// onerror handlers, embedded scripts) executes just from parsing —
// only from being inserted into the live document, which we never do
// with the unsanitized tree.
const RICH_TEXT_ALLOWED_TAGS = new Set([
    "B", "STRONG", "I", "EM", "U", "S", "STRIKE",
    "UL", "OL", "LI", "BR", "P", "DIV", "SPAN", "A",
]);
const RICH_TEXT_DROP_ENTIRELY = new Set([
    "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "SVG", "FORM",
]);
const RICH_TEXT_ALLOWED_ATTRS = { A: ["href"] };

function _sanitizeRichNode(root) {
    Array.from(root.childNodes).forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
            // Sanitize children first so an unwrapped disallowed wrapper
            // never re-exposes an un-sanitized descendant.
            _sanitizeRichNode(node);

            const tag = node.tagName;
            if (RICH_TEXT_DROP_ENTIRELY.has(tag)) {
                root.removeChild(node);
                return;
            }
            if (!RICH_TEXT_ALLOWED_TAGS.has(tag)) {
                // Unwrap: keep the (already-sanitized) text/children, drop the tag itself.
                while (node.firstChild) root.insertBefore(node.firstChild, node);
                root.removeChild(node);
                return;
            }

            const allowedAttrs = RICH_TEXT_ALLOWED_ATTRS[tag] || [];
            Array.from(node.attributes).forEach((attr) => {
                if (!allowedAttrs.includes(attr.name.toLowerCase())) {
                    node.removeAttribute(attr.name);
                }
            });
            if (tag === "A") {
                const href = (node.getAttribute("href") || "").trim();
                if (!/^(https?:|mailto:)/i.test(href)) {
                    node.removeAttribute("href");
                }
            }
        } else if (node.nodeType !== Node.TEXT_NODE) {
            // Comments, processing instructions, CDATA, etc. — no legitimate use here.
            root.removeChild(node);
        }
    });
}

function sanitizeRichHtml(html) {
    if (!html) return "";
    const template = document.createElement("template");
    template.innerHTML = String(html);
    _sanitizeRichNode(template.content);
    return template.innerHTML;
}


// ── Generic confirm / rename modals ───────────────────────────
// Replaces native confirm()/prompt() dialogs, which look out of place next
// to the app's own styled modal (#confirmation-modal, used for the import
// flow). Both return a Promise so call sites can `await` them like the
// browser built-ins they replace.

function showConfirmModal({ title = "Are you sure?", message = "", confirmText = "Confirm" } = {}) {
    return new Promise((resolve) => {
        const modal = document.getElementById("confirm-generic-modal");
        const titleEl = document.getElementById("confirm-generic-title");
        const msgEl = document.getElementById("confirm-generic-message");
        const confirmBtn = document.getElementById("confirm-generic-confirm");
        const cancelBtn = document.getElementById("confirm-generic-cancel");
        const closeBtn = document.getElementById("confirm-generic-close");

        titleEl.textContent = title;
        msgEl.textContent = message;
        confirmBtn.textContent = confirmText;

        const finish = (result) => {
            modal.classList.remove("active");
            confirmBtn.removeEventListener("click", onConfirm);
            cancelBtn.removeEventListener("click", onCancel);
            closeBtn.removeEventListener("click", onCancel);
            resolve(result);
        };
        const onConfirm = () => finish(true);
        const onCancel = () => finish(false);

        confirmBtn.addEventListener("click", onConfirm);
        cancelBtn.addEventListener("click", onCancel);
        closeBtn.addEventListener("click", onCancel);

        modal.classList.add("active");
    });
}

function showRenameModal({ title = "Rename Resume", initialValue = "" } = {}) {
    return new Promise((resolve) => {
        const modal = document.getElementById("rename-resume-modal");
        const titleEl = document.getElementById("rename-resume-title");
        const input = document.getElementById("rename-resume-input");
        const confirmBtn = document.getElementById("rename-resume-confirm");
        const cancelBtn = document.getElementById("rename-resume-cancel");
        const closeBtn = document.getElementById("rename-resume-close");

        titleEl.textContent = title;
        input.value = initialValue;

        const finish = (result) => {
            modal.classList.remove("active");
            confirmBtn.removeEventListener("click", onConfirm);
            cancelBtn.removeEventListener("click", onCancel);
            closeBtn.removeEventListener("click", onCancel);
            input.removeEventListener("keydown", onKeydown);
            resolve(result);
        };
        const onConfirm = () => {
            const value = input.value.trim();
            finish(value || null);
        };
        const onCancel = () => finish(null);
        const onKeydown = (e) => {
            if (e.key === "Enter") { e.preventDefault(); onConfirm(); }
            else if (e.key === "Escape") { onCancel(); }
        };

        confirmBtn.addEventListener("click", onConfirm);
        cancelBtn.addEventListener("click", onCancel);
        closeBtn.addEventListener("click", onCancel);
        input.addEventListener("keydown", onKeydown);

        modal.classList.add("active");
        setTimeout(() => { input.focus(); input.select(); }, 50);
    });
}


// ═══════════════════════════════════════════════════════════════
// 8. EVENT WIRING — connect buttons to functions
// ═══════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
    // ── App bootstrap ─────────────────────────────────────────
    // No auth required — load the profile and start the app directly.
    loadProfile();

    // ── Initial Icon Rendering ──────────────────────────────

    // ── Initial Icon Rendering ──────────────────────────────
    refreshIcons();

    // ── Theme Toggle Event Wiring ────────────────────────────
    document.querySelectorAll(".theme-toggle").forEach((btn) => {
        btn.addEventListener("click", () => {
            const currentTheme = document.documentElement.getAttribute("data-theme");
            const newTheme = currentTheme === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", newTheme);
            localStorage.setItem("theme", newTheme);
        });
    });

    // ── Collapsible Import Section Toggle ───────────────────
    const importTrigger = document.getElementById("import-accordion-trigger");
    const importContent = document.getElementById("import-collapse-content");
    const importChevron = document.getElementById("import-chevron");
    if (importTrigger && importContent && importChevron) {
        importTrigger.addEventListener("click", () => {
            const isVisible = window.getComputedStyle(importContent).display !== "none";
            if (isVisible) {
                importContent.style.display = "none";
                importChevron.classList.remove("open");
            } else {
                importContent.style.display = "block";
                importChevron.classList.add("open");
            }
            refreshIcons();
        });
    }

    // ── Save button ──────────────────────────────────────────
    saveBtn.addEventListener("click", saveProfile);
    generateBtn.addEventListener("click", generateResume);

    // ── Resume library ───────────────────────────────────────
    loadLibrary();
    document.getElementById("refresh-library-btn").addEventListener("click", loadLibrary);

    const librarySearchInput = document.getElementById("library-search-input");
    if (librarySearchInput) {
        librarySearchInput.addEventListener("input", () => {
            _librarySearchQuery = librarySearchInput.value;
            renderLibraryList();
        });
    }

    document.querySelectorAll(".library-filter-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".library-filter-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _libraryFilterType = btn.dataset.filter;
            renderLibraryList();
        });
    });

    // ── "Add" buttons ────────────────────────────────────────
    document.getElementById("add-link-btn").addEventListener("click", () => addLinkRow());
    document.getElementById("add-education-btn").addEventListener("click", () => addEducationItem());
    document.getElementById("add-experience-btn").addEventListener("click", () => addExperienceItem());
    document.getElementById("add-project-btn").addEventListener("click", () => addProjectItem());
    document.getElementById("add-certification-btn").addEventListener("click", () => addCertificationItem());

    document.getElementById("add-skill-category-btn").addEventListener("click", () => addSkillCategory());

    // ── AI Tailoring home button click check ─────────────────
    const homeTailorBtn = document.getElementById("btn-home-tailor");
    if (homeTailorBtn) {
        homeTailorBtn.addEventListener("click", () => {
            if (hasValidMasterResume) {
                window.location.hash = "#tailor";
            } else {
                showToast("Please create a Master Resume first (with name and email).", "error");
            }
        });
    }

    // ── Initial Router Setup & Listener ──────────────────────
    router();
    window.addEventListener("hashchange", router);

    // ── Keyboard shortcut: Ctrl+S to save ────────────────────
    // Context-aware: in the AI tailoring workspace, Ctrl+S force-saves the
    // active draft (the same thing debouncedSaveDraft does on a delay);
    // everywhere else it saves the master profile form.
    document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {
            e.preventDefault();
            if (document.body.classList.contains("workspace-active") && workspaceSessionId) {
                forceSaveDraft()
                    .then(() => showToast("Draft saved.", "success"))
                    .catch(err => showToast(err.message, "error"));
            } else {
                saveProfile();
            }
        }
    });

    // ── Tailor Your Resume View Events ──────────────────────────
    const jdTextarea = document.getElementById("tailor-jd-text");
    const jdUrlInput = document.getElementById("tailor-jd-url");
    const btnScrape = document.getElementById("tailor-btn-scrape");
    const btnGenerateTailored = document.getElementById("tailor-btn-generate");
    
    if (jdTextarea) {
        jdTextarea.addEventListener("input", () => {
            updateJdStatus();
            clearTimeout(analyzeJdTimeout);
            analyzeJdTimeout = setTimeout(() => {
                analyzeJobDescriptionText(jdTextarea.value.trim());
            }, 1200);
        });
        
        jdTextarea.addEventListener("blur", () => {
            updateJdStatus();
            analyzeJobDescriptionText(jdTextarea.value.trim());
        });
    }
    
    document.querySelectorAll("input[name='tailor-style']").forEach(radio => {
        radio.addEventListener("change", () => {
            document.querySelectorAll("input[name='tailor-style']").forEach(input => {
                const card = input.closest(".style-radio-card");
                if (card) {
                    card.classList.toggle("active", input.checked);
                }
            });
        });
    });

    // Initialize custom style preference slider
    setupStyleSlider();
    
    if (btnScrape && jdUrlInput && jdTextarea) {
        btnScrape.addEventListener("click", async () => {
            const url = jdUrlInput.value.trim();
            if (!url) {
                showToast("Please enter a valid job posting URL first.", "error");
                return;
            }
            
            btnScrape.disabled = true;
            const originalBtnHtml = btnScrape.innerHTML;
            btnScrape.innerHTML = `<span class="loading-spinner" style="width: 14px; height: 14px; border-width: 2px; margin: 0; display: inline-block; vertical-align: middle;"></span> <span>Fetching...</span>`;
            jdTextarea.disabled = true;
            jdUrlInput.disabled = true;
            
            try {
                const response = await fetch("/api/scrape-job", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url: url })
                });
                
                const result = await response.json();
                if (response.ok && result.status === "ok") {
                    jdTextarea.value = result.job_description;
                    updateJdStatus();
                    showToast("Job description fetched and loaded!", "success");
                    analyzeJobDescriptionText(result.job_description);
                } else {
                    const errMsg = result.error || "This website doesn't allow automated extraction. Please copy and paste the job description below instead.";
                    showToast(errMsg, "error");
                    jdTextarea.focus();
                }
            } catch (err) {
                showToast("This website doesn't allow automated extraction. Please copy and paste the job description below instead.", "error");
                jdTextarea.focus();
            } finally {
                btnScrape.disabled = false;
                btnScrape.innerHTML = originalBtnHtml;
                jdTextarea.disabled = false;
                jdUrlInput.disabled = false;
                refreshIcons();
            }
        });
    }
    
    if (btnGenerateTailored) {
        btnGenerateTailored.addEventListener("click", () => {
            const styleRadio = document.querySelector("input[name='tailor-style']:checked");
            const style = styleRadio ? styleRadio.value : "balanced";
            
            const focus_areas = [];
            ["skills", "projects", "experience", "summary"].forEach(area => {
                const cb = document.getElementById(`focus-${area}`);
                if (cb && cb.checked) focus_areas.push(area);
            });
            
            const job_level = document.getElementById("tailor-job-level").value;
            
            const nameVal = document.getElementById("tailor-pi-name").value.trim();
            const emailVal = document.getElementById("tailor-pi-email").value.trim();
            const phoneVal = document.getElementById("tailor-pi-phone").value.trim();
            
            const jdText = document.getElementById("tailor-jd-text").value.trim();
            const jdUrl = document.getElementById("tailor-jd-url").value.trim();
            
            const setupState = {
                contact_info: {
                    name: nameVal,
                    email: emailVal,
                    phone: phoneVal
                },
                job_description: jdText,
                job_url: jdUrl,
                preferences: {
                    style: style,
                    focus_areas: focus_areas,
                    job_level: job_level
                }
            };
            
            sessionStorage.setItem("tailorSetupState", JSON.stringify(setupState));
            window.location.hash = "#tailor-workspace";
        });
    }

    // ── Remove transitions blocker ───────────────────────────
    // Allow the browser to paint the initial styled page before enabling theme transitions
    setTimeout(() => {
        document.body.classList.remove("no-transition");
    }, 100);
});

// ── Global Helper Functions for Tailoring View ────────────────
function setupStyleSlider() {
    const slider = document.getElementById("style-slider");
    if (!slider) return;

    const styles = ["conservative", "balanced", "aggressive"];

    function updateSliderUI(val) {
        // Update ticks active classes
        document.querySelectorAll(".tick-mark").forEach(tick => {
            const tickIdx = parseInt(tick.dataset.index);
            tick.classList.toggle("active", tickIdx === val);
        });

        // Update labels active classes
        document.querySelectorAll(".slider-label-item").forEach(label => {
            const labelIdx = parseInt(label.dataset.index);
            label.classList.toggle("active", labelIdx === val);
        });

        // Update descriptions active classes
        document.querySelectorAll(".slider-desc-card").forEach(desc => {
            const descIdx = parseInt(desc.dataset.index);
            desc.classList.toggle("active", descIdx === val);
        });
    }

    // Sync from slider to radios
    slider.addEventListener("input", () => {
        const val = parseInt(slider.value);
        updateSliderUI(val);
        
        const styleName = styles[val - 1];
        const radio = document.getElementById(`style-${styleName}`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event("change"));
        }
    });

    // Handle clicks on label items
    document.querySelectorAll(".slider-label-item").forEach(item => {
        item.addEventListener("click", () => {
            const val = parseInt(item.dataset.index);
            slider.value = val;
            updateSliderUI(val);
            
            const styleName = styles[val - 1];
            const radio = document.getElementById(`style-${styleName}`);
            if (radio) {
                radio.checked = true;
                radio.dispatchEvent(new Event("change"));
            }
        });
    });

    // Sync from radios to slider (for programmatically updating from cached profile or session storage)
    document.querySelectorAll("input[name='tailor-style']").forEach(radio => {
        radio.addEventListener("change", () => {
            if (radio.checked) {
                const val = styles.indexOf(radio.value) + 1;
                if (val > 0) {
                    slider.value = val;
                    updateSliderUI(val);
                }
            }
        });
    });
    
    // Initial sync
    const checkedRadio = document.querySelector("input[name='tailor-style']:checked");
    if (checkedRadio) {
        const val = styles.indexOf(checkedRadio.value) + 1;
        if (val > 0) {
            slider.value = val;
            updateSliderUI(val);
        }
    }
}
let analyzeJdTimeout = null;

function updateJdStatus() {
    const jdTextarea = document.getElementById("tailor-jd-text");
    if (!jdTextarea) return;
    
    const jdText = jdTextarea.value.trim();
    const statusDiv = document.getElementById("tailor-jd-status");
    const statusText = document.getElementById("tailor-jd-status-text");
    
    if (jdText.length > 50) {
        const wordCount = jdText.split(/\s+/).filter(Boolean).length;
        if (statusText) statusText.textContent = `Job Description Ready ✓ (${wordCount} words)`;
        if (statusDiv) statusDiv.style.display = "flex";
    } else {
        if (statusDiv) statusDiv.style.display = "none";
    }
    updateTailorChecklist();
}

function updateTailorChecklist() {
    const masterReq = document.getElementById("req-master-resume");
    const jdReq = document.getElementById("req-job-description");
    const generateBtn = document.getElementById("tailor-btn-generate");
    
    let isMasterOk = false;
    let isJdOk = false;
    
    // Master resume check
    if (masterReq) {
        const icon = masterReq.querySelector(".req-icon");
        const statusText = masterReq.querySelector(".req-status-text");
        
        if (hasValidMasterResume) {
            if (icon) {
                icon.setAttribute("class", "req-icon status-ok");
                icon.setAttribute("data-lucide", "check-circle");
            }
            if (statusText) {
                statusText.textContent = "Saved";
                statusText.style.color = "var(--success)";
            }
            isMasterOk = true;
        } else {
            if (icon) {
                icon.setAttribute("class", "req-icon status-missing");
                icon.setAttribute("data-lucide", "alert-circle");
            }
            if (statusText) {
                statusText.textContent = "Missing";
                statusText.style.color = "var(--error)";
            }
        }
    }
    
    // Job Description check
    const jdTextarea = document.getElementById("tailor-jd-text");
    const jdText = jdTextarea ? jdTextarea.value.trim() : "";
    
    if (jdReq) {
        const icon = jdReq.querySelector(".req-icon");
        const statusText = jdReq.querySelector(".req-status-text");
        
        if (jdText.length > 50) {
            if (icon) {
                icon.setAttribute("class", "req-icon status-ok");
                icon.setAttribute("data-lucide", "check-circle");
            }
            if (statusText) {
                statusText.textContent = "Provided";
                statusText.style.color = "var(--success)";
            }
            isJdOk = true;
        } else {
            if (icon) {
                icon.setAttribute("class", "req-icon status-waiting");
                icon.setAttribute("data-lucide", "circle-dashed");
            }
            if (statusText) {
                statusText.textContent = "Missing";
                statusText.style.color = "var(--text-muted)";
            }
        }
    }
    
    if (generateBtn) {
        generateBtn.disabled = !(isMasterOk && isJdOk);
    }
    
    refreshIcons();
}

async function analyzeJobDescriptionText(jdText) {
    if (!jdText || jdText.length < 100) return;
    
    const skillsCard = document.getElementById("tailor-skills-card");
    const techList = document.getElementById("detected-tech-skills");
    const keywordsList = document.getElementById("detected-keywords");
    
    if (!skillsCard || !techList || !keywordsList) return;
    
    try {
        const response = await fetch("/api/analyze-job", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_description: jdText })
        });
        
        if (!response.ok) throw new Error("Analysis failed");
        const result = await response.json();
        
        if (result.status === "ok" && (result.skills.length > 0 || result.keywords.length > 0)) {
            techList.innerHTML = "";
            keywordsList.innerHTML = "";
            
            result.skills.forEach(skill => {
                const badge = document.createElement("span");
                badge.className = "skill-badge";
                badge.textContent = skill;
                techList.appendChild(badge);
            });
            if (result.skills.length === 0) {
                techList.innerHTML = `<span style="color: var(--text-muted); font-size: 0.85rem;">None detected</span>`;
            }
            
            result.keywords.forEach(kw => {
                const badge = document.createElement("span");
                badge.className = "skill-badge";
                badge.textContent = kw;
                keywordsList.appendChild(badge);
            });
            if (result.keywords.length === 0) {
                keywordsList.innerHTML = `<span style="color: var(--text-muted); font-size: 0.85rem;">None detected</span>`;
            }
            
            skillsCard.style.display = "block";
            refreshIcons();
        }
    } catch (err) {
        console.error("Lightweight job description analysis error:", err);
    }
}


// ── AI Resume Workspace View Controllers ──────────────────────
// workspaceSessionId is mirrored into sessionStorage (see setWorkspaceSessionId
// / clearWorkspaceSessionId below) so an in-progress tailoring draft survives
// a page refresh — the draft itself already lives on disk under
// storage/sessions/<id>/, this just lets the UI find its way back to it.
const WORKSPACE_SESSION_STORAGE_KEY = "workspaceSessionId";
let workspaceSessionId = sessionStorage.getItem(WORKSPACE_SESSION_STORAGE_KEY) || null;
let currentProfileJson = null;
let autoSaveTimeout = null;
let isEventsWired = false;

function setWorkspaceSessionId(sessionId) {
    workspaceSessionId = sessionId;
    if (sessionId) {
        sessionStorage.setItem(WORKSPACE_SESSION_STORAGE_KEY, sessionId);
    } else {
        sessionStorage.removeItem(WORKSPACE_SESSION_STORAGE_KEY);
    }
}

function setStepState(stepId, state) {
    const stepEl = document.getElementById(stepId);
    if (!stepEl) return;
    
    const icon = stepEl.querySelector(".progress-icon");
    if (!icon) return;
    
    stepEl.classList.remove("active", "done");
    
    if (state === "waiting") {
        icon.setAttribute("class", "progress-icon step-waiting");
        icon.setAttribute("data-lucide", "circle-dashed");
    } else if (state === "active") {
        stepEl.classList.add("active");
        icon.setAttribute("class", "progress-icon step-active");
        icon.setAttribute("data-lucide", "loader");
    } else if (state === "done") {
        stepEl.classList.add("done");
        icon.setAttribute("class", "progress-icon step-done");
        icon.setAttribute("data-lucide", "check-circle");
    }
    refreshIcons();
}

function animateLoadingSteps() {
    return new Promise(resolve => {
        let step = 0;
        const steps = [
            "step-read-master",
            "step-understand-jd",
            "step-id-keywords",
            "step-optimize-res",
            "step-build-prev"
        ];
        
        // Reset all steps to waiting
        steps.forEach(s => setStepState(s, "waiting"));
        
        const nextStep = () => {
            if (step > 0) {
                setStepState(steps[step - 1], "done");
            }
            if (step < steps.length) {
                setStepState(steps[step], "active");
                step++;
                setTimeout(nextStep, 700);
            } else {
                resolve();
            }
        };
        nextStep();
    });
}

async function initializeTailoringWorkspace() {
    const setupStateStr = sessionStorage.getItem("tailorSetupState");
    
    // If no setup parameters in session storage and no active session id, go back to setup
    if (!setupStateStr && !workspaceSessionId) {
        window.location.hash = "#tailor";
        return;
    }
    
    // Wire up events once
    setupWorkspaceEventsOnce();
    
    if (setupStateStr) {
        // Clear it so it doesn't trigger on every hash router check
        sessionStorage.removeItem("tailorSetupState");
        
        const setupState = JSON.parse(setupStateStr);
        
        // Show Stage 1 Loading overlay
        document.getElementById("workspace-loading").style.display = "flex";
        document.getElementById("workspace-content").style.display = "none";
        document.getElementById("workspace-bottom-bar").style.display = "none";
        
        // Kick off animations and AJAX in parallel
        const animPromise = animateLoadingSteps();
        
        let wsData = null;
        let wsError = null;
        
        try {
            const response = await fetch("/api/tailor/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(setupState)
            });
            
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || "Failed to customize resume.");
            }
            wsData = await response.json();
        } catch (err) {
            wsError = err.message;
        }
        
        // Wait for sequencer to finish
        await animPromise;
        
        if (wsError) {
            showToast(wsError, "error");
            window.location.hash = "#tailor";
            return;
        }
        
        // Initialize state variables
        setWorkspaceSessionId(wsData.session_id);

        // Fetch the full draft (with profile) to render
        await fetchAndRenderWorkspace(workspaceSessionId);

        // Hide Stage 1 Loading and reveal Stage 2
        document.getElementById("workspace-loading").style.display = "none";
        document.getElementById("workspace-content").style.display = "flex";
        document.getElementById("workspace-bottom-bar").style.display = "flex";

        // Initialize chat history with welcome message
        initChatHistory();

        showToast("Tailored resume generated!", "success");
    } else if (workspaceSessionId) {
        // Either a same-tab hash change back into the workspace, or a fresh
        // page load that recovered workspaceSessionId from sessionStorage
        // (e.g. the user refreshed mid-session) — reload the draft in place.
        document.getElementById("workspace-loading").style.display = "none";
        document.getElementById("workspace-content").style.display = "flex";
        document.getElementById("workspace-bottom-bar").style.display = "flex";

        const ok = await fetchAndRenderWorkspace(workspaceSessionId);
        if (ok) {
            initChatHistory();
        } else {
            // The session may have been cleaned up server-side (see
            // services/session_service.py's expiry sweep) — fall back to setup
            // instead of leaving the user staring at an empty workspace.
            setWorkspaceSessionId(null);
            window.location.hash = "#tailor";
        }
    }
}

async function fetchAndRenderWorkspace(sessionId) {
    try {
        const response = await fetch(`/api/tailor/draft/${sessionId}`);
        if (!response.ok) throw new Error("Failed to fetch draft data.");
        const data = await response.json();

        currentProfileJson = data.profile;
        renderWorkspaceData(data);
        setSaveStatus("saved"); // reset indicator to a clean baseline on (re)load
        return true;
    } catch (err) {
        showToast(err.message, "error");
        return false;
    }
}

function renderWorkspaceData(data) {
    // 1. Update Resume Insights (stats)
    document.getElementById("stat-sections-improved").textContent = data.stats.sections_reordered || 0;
    document.getElementById("stat-bullets-improved").textContent = data.stats.bullets_improved || 0;
    document.getElementById("stat-kw-added").textContent = data.stats.keywords_added || 0;
    document.getElementById("stat-kw-missing").textContent = data.stats.keywords_not_included || 0;
    
    // 2. Expandable Suggestions Accordion
    const suggestionsList = document.getElementById("ws-suggestions-list");
    suggestionsList.innerHTML = "";
    if (!data.suggestions || data.suggestions.length === 0) {
        suggestionsList.innerHTML = `<p style="font-size: 0.85rem; color: var(--text-muted); padding: var(--space-sm) 0;">No suggestions needed. The resume is fully aligned!</p>`;
    } else {
        data.suggestions.forEach(s => {
            const item = document.createElement("div");
            item.className = "suggestion-item";
            item.innerHTML = `
                <button class="suggestion-header" type="button">
                    <div>
                        <span class="suggestion-badge">${escapeHtml(s.section)}</span>
                        <span>${escapeHtml(s.title)}</span>
                    </div>
                    <i data-lucide="chevron-down" class="suggestion-chevron"></i>
                </button>
                <div class="suggestion-content">
                    <p>${escapeHtml(s.explanation)}</p>
                </div>
            `;
            
            // Toggle open on click
            item.querySelector(".suggestion-header").addEventListener("click", () => {
                const wasOpen = item.classList.contains("open");
                // Close others
                suggestionsList.querySelectorAll(".suggestion-item").forEach(el => el.classList.remove("open"));
                if (!wasOpen) {
                    item.classList.add("open");
                }
            });
            
            suggestionsList.appendChild(item);
        });
    }
    
    // 3. Keywords Not Included Badges
    const missingBadges = document.getElementById("ws-missing-badges");
    missingBadges.innerHTML = "";
    if (!data.keywords_not_included_list || data.keywords_not_included_list.length === 0) {
        missingBadges.innerHTML = `<span style="font-size: 0.85rem; color: var(--text-muted);">None</span>`;
    } else {
        data.keywords_not_included_list.forEach(skill => {
            const badge = document.createElement("span");
            badge.className = "skill-badge";
            badge.style.backgroundColor = "var(--bg-surface)";
            badge.style.border = "1px solid var(--warning)";
            badge.style.color = "var(--warning)";
            badge.textContent = skill;
            missingBadges.appendChild(badge);
        });
    }
    
    // 4. Update the editable resume editor
    renderResumeEditor(data.profile);
    refreshIcons();
}

function initChatHistory() {
    const historyBox = document.getElementById("ws-chat-history");
    historyBox.innerHTML = `
        <div class="chat-message assistant">
            Hi! I've completed the initial resume tailoring according to your target job. 
            Review the suggestions and insights above, edit anything in the document directly, or ask me to make specific refinements here!
        </div>
    `;
    historyBox.scrollTop = historyBox.scrollHeight;
}

async function handleChatSubmit(e) {
    if (e) e.preventDefault();
    
    const input = document.getElementById("ws-chat-input");
    const msg = input.value.trim();
    if (!msg) return;
    
    input.value = "";
    if (input.tagName === "TEXTAREA") {
        input.style.height = "";
    }
    const charCounter = document.getElementById("chat-char-counter");
    if (charCounter) {
        charCounter.textContent = "0/500";
    }
    input.disabled = true;
    const sendBtn = document.getElementById("ws-chat-send");
    sendBtn.disabled = true;
    
    const historyBox = document.getElementById("ws-chat-history");
    
    // Append User Bubble
    const userMsg = document.createElement("div");
    userMsg.className = "chat-message user";
    userMsg.textContent = msg;
    historyBox.appendChild(userMsg);
    historyBox.scrollTop = historyBox.scrollHeight;
    
    // Append Assistant Loader Bubble
    const loaderMsg = document.createElement("div");
    loaderMsg.className = "chat-message assistant loader-msg";
    loaderMsg.innerHTML = `
        <div class="pulsing-bubble">
            <div class="pulse-dot"></div>
            <div class="pulse-dot"></div>
            <div class="pulse-dot"></div>
        </div>
    `;
    historyBox.appendChild(loaderMsg);
    historyBox.scrollTop = historyBox.scrollHeight;
    
    try {
        // Force sync manual edits before submitting chat instructions
        await forceSaveDraft();

        const response = await fetch("/api/tailor/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: workspaceSessionId,
                message: msg
            })
        });
        
        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.error || "Failed to update draft.");
        }
        
        const data = await response.json();
        
        // Remove loader bubble
        loaderMsg.remove();
        
        // Append Assistant Explanation bubble
        const assistantMsg = document.createElement("div");
        assistantMsg.className = "chat-message assistant";
        
        if (data.suggestions && data.suggestions.length > 0) {
            const intro = document.createElement("div");
            intro.className = "chat-intro";
            intro.textContent = "Done! Here is what I changed and why:";
            assistantMsg.appendChild(intro);

            const list = document.createElement("ul");
            list.className = "chat-suggestions-list";
            data.suggestions.forEach(s => {
                const li = document.createElement("li");
                const titleEl = document.createElement("strong");
                titleEl.textContent = s.title + ": ";
                const explEl = document.createElement("span");
                explEl.textContent = s.explanation;
                li.appendChild(titleEl);
                li.appendChild(explEl);
                list.appendChild(li);
            });
            assistantMsg.appendChild(list);
        } else {
            assistantMsg.textContent = "I've revised the resume based on your instructions.";
        }
        
        historyBox.appendChild(assistantMsg);
        historyBox.scrollTop = historyBox.scrollHeight;
        
        // Fetch updated draft (profile + metadata)
        const draftResponse = await fetch(`/api/tailor/draft/${workspaceSessionId}`);
        if (!draftResponse.ok) throw new Error("Failed to fetch refined draft data.");
        const updatedData = await draftResponse.json();
        
        const prevProfile = currentProfileJson;
        currentProfileJson = updatedData.profile;
        
        // Re-render
        renderWorkspaceData(updatedData);
        
        // Visual feedback highlight
        highlightChangedSections(prevProfile, updatedData.profile);
        
        showToast("Resume revised successfully!", "success");
        
    } catch (err) {
        if (loaderMsg.parentNode) loaderMsg.remove();
        showToast(err.message, "error");
        
        const assistantMsg = document.createElement("div");
        assistantMsg.className = "chat-message assistant";
        assistantMsg.textContent = "Sorry, I encountered an error while trying to process your request.";
        historyBox.appendChild(assistantMsg);
        historyBox.scrollTop = historyBox.scrollHeight;
    } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
        refreshIcons();
    }
}

function highlightChangedSections(prev, curr) {
    if (!prev || !curr) return;
    
    const triggerHighlight = (id) => {
        const el = document.getElementById(id);
        if (el) {
            el.classList.add("ai-highlight");
            setTimeout(() => {
                el.classList.remove("ai-highlight");
            }, 3000);
        }
    };

    if (prev.achievements !== curr.achievements) {
        triggerHighlight("editor-section-achievements");
    }
    if (JSON.stringify(prev.personal_info) !== JSON.stringify(curr.personal_info)) {
        triggerHighlight("editor-section-personal");
    }
    if (JSON.stringify(prev.education) !== JSON.stringify(curr.education)) {
        triggerHighlight("editor-section-education");
    }
    if (JSON.stringify(prev.experience) !== JSON.stringify(curr.experience)) {
        triggerHighlight("editor-section-experience");
    }
    if (JSON.stringify(prev.projects) !== JSON.stringify(curr.projects)) {
        triggerHighlight("editor-section-projects");
    }
    if (JSON.stringify(prev.skills) !== JSON.stringify(curr.skills)) {
        triggerHighlight("editor-section-skills");
    }
    if (JSON.stringify(prev.certifications) !== JSON.stringify(curr.certifications)) {
        triggerHighlight("editor-section-certifications");
    }
}

function renderResumeEditor(profile) {
    const editor = document.getElementById("resume-editor");
    if (!editor) return;

    let html = "";

    // 1. Personal Info Section
    const pi = profile.personal_info || { name: "", email: "", phone: "", links: {} };
    html += `
        <div class="editor-section" id="editor-section-personal">
            <div class="editor-section-body text-center">
                <h1 id="editor-pi-name" class="editor-name font-bold" contenteditable="true" placeholder="Your Name">${escapeHtml(pi.name)}</h1>
                <div class="editor-contact-info">
                    <span id="editor-pi-email" contenteditable="true" placeholder="email@domain.com">${escapeHtml(pi.email)}</span>
                    <span class="editor-separator"> | </span>
                    <span id="editor-pi-phone" contenteditable="true" placeholder="Phone Number">${escapeHtml(pi.phone || "")}</span>
                </div>
                <div class="editor-links-container" id="editor-links-list">
    `;
    
    // Render links
    const links = pi.links || {};
    Object.entries(links).forEach(([label, url]) => {
        html += `
            <span class="editor-link-item" data-label="${escapeHtml(label)}">
                <span class="editor-separator"> | </span>
                <span class="link-label font-semibold" contenteditable="true">${escapeHtml(label)}</span>: 
                <span class="link-url" contenteditable="true">${escapeHtml(url)}</span>
                <button type="button" class="btn-delete-link" title="Remove Link">×</button>
            </span>
        `;
    });
    
    html += `
                    <button type="button" class="btn-add-link" style="margin-left: 8px;">+ Add Link</button>
                </div>
            </div>
        </div>
    `;

    // Helper for Section Headers
    const renderSectionHeader = (title, addBtnId, addBtnText) => {
        return `
            <div class="editor-section-header-row flex justify-between items-center">
                <h2 class="editor-section-header">${title}</h2>
                ${addBtnId ? `<button type="button" id="${addBtnId}" class="btn-add-entry"><i data-lucide="plus"></i> ${addBtnText}</button>` : ""}
            </div>
        `;
    };

    // 2. Education Section
    html += `
        <div class="editor-section" id="editor-section-education">
            ${renderSectionHeader("Education", "btn-editor-add-edu", "Add Education")}
            <div id="editor-education-list">
    `;
    (profile.education || []).forEach((edu, idx) => {
        html += `
            <div class="editor-edu-entry editor-entry" data-index="${idx}">
                <div class="editor-entry-row flex justify-between">
                    <div class="editor-entry-header-left">
                        <span class="edu-institution font-bold" contenteditable="true" placeholder="Institution">${escapeHtml(edu.institution)}</span>
                        <span class="editor-separator"> — </span>
                        <span class="edu-degree font-semibold" contenteditable="true" placeholder="Degree">${escapeHtml(edu.degree)}</span>
                    </div>
                    <div class="editor-entry-header-right flex items-center gap-sm">
                        <span class="edu-start-date" contenteditable="true" placeholder="Start Date">${escapeHtml(edu.start_date)}</span>
                        <span class="editor-separator"> — </span>
                        <span class="edu-end-date" contenteditable="true" placeholder="End Date">${escapeHtml(edu.end_date)}</span>
                        <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                    </div>
                </div>
                <div class="editor-entry-details flex gap-md">
                    <div>
                        <span class="footer-label">GPA: </span>
                        <span class="edu-gpa" contenteditable="true" placeholder="Omit or e.g. 3.9">${edu.gpa !== null && edu.gpa !== undefined ? edu.gpa : ""}</span>
                    </div>
                    <div class="flex-grow">
                        <span class="footer-label">Coursework: </span>
                        <span class="edu-coursework" contenteditable="true" placeholder="e.g. Algorithms, Databases">${escapeHtml((edu.coursework || []).join(", "))}</span>
                    </div>
                </div>
            </div>
        `;
    });
    html += `
            </div>
        </div>
    `;

    // 3. Experience Section
    html += `
        <div class="editor-section" id="editor-section-experience">
            ${renderSectionHeader("Work Experience", "btn-editor-add-exp", "Add Job")}
            <div id="editor-experience-list">
    `;
    (profile.experience || []).forEach((exp, idx) => {
        html += `
            <div class="editor-exp-entry editor-entry" data-index="${idx}">
                <div class="editor-entry-row flex justify-between">
                    <div class="editor-entry-header-left">
                        <span class="exp-role font-bold" contenteditable="true" placeholder="Role">${escapeHtml(exp.role)}</span>
                        <span class="editor-separator"> at </span>
                        <span class="exp-company font-bold" contenteditable="true" placeholder="Company">${escapeHtml(exp.company)}</span>
                        <span class="exp-work-mode italic" contenteditable="true" placeholder="Remote/Onsite">${escapeHtml(exp.work_mode || "")}</span>
                    </div>
                    <div class="editor-entry-header-right flex items-center gap-sm">
                        <span class="exp-start-date" contenteditable="true" placeholder="Start Date">${escapeHtml(exp.start_date)}</span>
                        <span class="editor-separator"> — </span>
                        <span class="exp-end-date" contenteditable="true" placeholder="End Date">${escapeHtml(exp.end_date)}</span>
                        <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                    </div>
                </div>
                <div class="editor-bullets-container">
                    <ul class="editor-bullets-list">
        `;
        (exp.bullets || []).forEach((bullet) => {
            html += `
                <li class="editor-bullet-item">
                    <span class="bullet-dot">•</span>
                    <div class="editor-bullet-content" contenteditable="true" placeholder="Add details...">${sanitizeRichHtml(bullet)}</div>
                    <button type="button" class="btn-delete-bullet" title="Remove Bullet">×</button>
                </li>
            `;
        });
        html += `
                    </ul>
                    <button type="button" class="btn-add-bullet">+ Add Bullet</button>
                </div>
                <div class="editor-entry-footer">
                    <span class="footer-label">Technologies: </span>
                    <span class="exp-technologies" contenteditable="true" placeholder="e.g. React, Python">${escapeHtml((exp.technologies || []).join(", "))}</span>
                </div>
            </div>
        `;
    });
    html += `
            </div>
        </div>
    `;

    // 4. Projects Section
    html += `
        <div class="editor-section" id="editor-section-projects">
            ${renderSectionHeader("Projects", "btn-editor-add-proj", "Add Project")}
            <div id="editor-projects-list">
    `;
    (profile.projects || []).forEach((proj, idx) => {
        html += `
            <div class="editor-proj-entry editor-entry" data-index="${idx}">
                <div class="editor-entry-row flex justify-between">
                    <div class="editor-entry-header-left">
                        <span class="proj-name font-bold" contenteditable="true" placeholder="Project Name">${escapeHtml(proj.name)}</span>
                        <span class="editor-separator"> | </span>
                        <span class="proj-link italic" contenteditable="true" placeholder="Project Link">${escapeHtml(proj.link || "")}</span>
                    </div>
                    <div class="editor-entry-header-right flex items-center gap-sm">
                        <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                    </div>
                </div>
                <div class="proj-description-container">
                    <div class="proj-description text-muted" contenteditable="true" placeholder="Project One-liner Description...">${sanitizeRichHtml(proj.description)}</div>
                </div>
                <div class="editor-bullets-container">
                    <ul class="editor-bullets-list">
        `;
        (proj.bullets || []).forEach((bullet) => {
            html += `
                <li class="editor-bullet-item">
                    <span class="bullet-dot">•</span>
                    <div class="editor-bullet-content" contenteditable="true" placeholder="Add details...">${sanitizeRichHtml(bullet)}</div>
                    <button type="button" class="btn-delete-bullet" title="Remove Bullet">×</button>
                </li>
            `;
        });
        html += `
                    </ul>
                    <button type="button" class="btn-add-bullet">+ Add Bullet</button>
                </div>
                <div class="editor-entry-footer">
                    <span class="footer-label">Technologies: </span>
                    <span class="proj-technologies" contenteditable="true" placeholder="e.g. React, Node.js">${escapeHtml((proj.technologies || []).join(", "))}</span>
                </div>
            </div>
        `;
    });
    html += `
            </div>
        </div>
    `;

    // 5. Skills Section
    html += `
        <div class="editor-section" id="editor-section-skills">
            ${renderSectionHeader("Technical Skills", "btn-editor-add-skill-cat", "Add Category")}
            <div id="editor-skills-categories" class="skills-editor-grid">
    `;
    const skillCats = (profile.skills && profile.skills.categories) || {};
    Object.entries(skillCats).forEach(([catName, skillsList]) => {
        html += `
            <div class="editor-skill-category editor-entry flex" data-category="${escapeHtml(catName)}">
                <span class="skill-category-name font-bold" contenteditable="true" placeholder="Category Name">${escapeHtml(catName)}</span>
                <span class="editor-separator">: </span>
                <span class="skill-category-skills flex-grow" contenteditable="true" placeholder="Skills (comma separated)">${escapeHtml(skillsList.join(", "))}</span>
                <button type="button" class="btn-delete-entry" title="Remove Category"><i data-lucide="trash-2"></i></button>
            </div>
        `;
    });
    html += `
            </div>
        </div>
    `;

    // 6. Certifications Section
    html += `
        <div class="editor-section" id="editor-section-certifications">
            ${renderSectionHeader("Certifications", "btn-editor-add-cert", "Add Certification")}
            <div id="editor-certifications-list">
    `;
    (profile.certifications || []).forEach((cert, idx) => {
        html += `
            <div class="editor-cert-entry editor-entry" data-index="${idx}">
                <div class="editor-entry-row flex justify-between">
                    <div class="editor-entry-header-left">
                        <span class="cert-name font-bold" contenteditable="true" placeholder="Certification Name">${escapeHtml(cert.name)}</span>
                        <span class="editor-separator"> by </span>
                        <span class="cert-issuer font-semibold" contenteditable="true" placeholder="Issuer">${escapeHtml(cert.issuer)}</span>
                    </div>
                    <div class="editor-entry-header-right flex items-center gap-sm">
                        <span class="cert-date" contenteditable="true" placeholder="Date">${escapeHtml(cert.date)}</span>
                        <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                    </div>
                </div>
                <div class="cert-description text-muted" contenteditable="true" placeholder="Optional description...">${sanitizeRichHtml(cert.description)}</div>
            </div>
        `;
    });
    html += `
            </div>
        </div>
    `;

    // 7. Achievements Section
    html += `
        <div class="editor-section" id="editor-section-achievements">
            ${renderSectionHeader("Achievements & Awards")}
            <div id="editor-achievements-text" class="editor-achievements-content" contenteditable="true" placeholder="Type your achievements here...">${sanitizeRichHtml(profile.achievements)}</div>
        </div>
    `;

    editor.innerHTML = html;
    
    wireEditorEvents();
}

function wireEditorEvents() {
    const editor = document.getElementById("resume-editor");
    if (!editor) return;

    editor.addEventListener("input", () => {
        debouncedSaveDraft();
    });

    editor.addEventListener("click", (e) => {
        const btnDeleteBullet = e.target.closest(".btn-delete-bullet");
        if (btnDeleteBullet) {
            const li = btnDeleteBullet.closest(".editor-bullet-item");
            if (li) {
                li.remove();
                debouncedSaveDraft();
            }
            return;
        }

        const btnAddBullet = e.target.closest(".btn-add-bullet");
        if (btnAddBullet) {
            const ul = btnAddBullet.previousElementSibling;
            if (ul && ul.classList.contains("editor-bullets-list")) {
                const li = document.createElement("li");
                li.className = "editor-bullet-item";
                li.innerHTML = `
                    <span class="bullet-dot">•</span>
                    <div class="editor-bullet-content" contenteditable="true" placeholder="Add details..."></div>
                    <button type="button" class="btn-delete-bullet" title="Remove Bullet">×</button>
                `;
                ul.appendChild(li);
                li.querySelector(".editor-bullet-content").focus();
                debouncedSaveDraft();
            }
            return;
        }

        const btnDeleteEntry = e.target.closest(".btn-delete-entry");
        if (btnDeleteEntry) {
            const entry = btnDeleteEntry.closest(".editor-entry");
            if (entry) {
                entry.remove();
                debouncedSaveDraft();
            }
            return;
        }

        const btnDeleteLink = e.target.closest(".btn-delete-link");
        if (btnDeleteLink) {
            const span = btnDeleteLink.closest(".editor-link-item");
            if (span) {
                span.remove();
                debouncedSaveDraft();
            }
            return;
        }

        const btnAddLink = e.target.closest(".btn-add-link");
        if (btnAddLink) {
            const container = document.getElementById("editor-links-list");
            if (container) {
                const span = document.createElement("span");
                span.className = "editor-link-item";
                span.dataset.label = "GitHub";
                span.innerHTML = `
                    <span class="editor-separator"> | </span>
                    <span class="link-label font-semibold" contenteditable="true">Portfolio</span>: 
                    <span class="link-url" contenteditable="true">https://example.com</span>
                    <button type="button" class="btn-delete-link" title="Remove Link">×</button>
                `;
                container.insertBefore(span, btnAddLink);
                span.querySelector(".link-label").focus();
                debouncedSaveDraft();
            }
            return;
        }
    });

    const btnAddExp = document.getElementById("btn-editor-add-exp");
    if (btnAddExp) {
        btnAddExp.addEventListener("click", () => {
            const list = document.getElementById("editor-experience-list");
            if (list) {
                const entry = document.createElement("div");
                entry.className = "editor-exp-entry editor-entry";
                entry.innerHTML = `
                    <div class="editor-entry-row flex justify-between">
                        <div class="editor-entry-header-left">
                            <span class="exp-role font-bold" contenteditable="true" placeholder="Role">Software Engineer</span>
                            <span class="editor-separator"> at </span>
                            <span class="exp-company font-bold" contenteditable="true" placeholder="Company">Company</span>
                            <span class="exp-work-mode italic" contenteditable="true" placeholder="Remote/Onsite">Remote</span>
                        </div>
                        <div class="editor-entry-header-right flex items-center gap-sm">
                            <span class="exp-start-date" contenteditable="true" placeholder="Start Date">Jan 2024</span>
                            <span class="editor-separator"> — </span>
                            <span class="exp-end-date" contenteditable="true" placeholder="End Date">Present</span>
                            <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                        </div>
                    </div>
                    <div class="editor-bullets-container">
                        <ul class="editor-bullets-list">
                            <li class="editor-bullet-item">
                                <span class="bullet-dot">•</span>
                                <div class="editor-bullet-content" contenteditable="true" placeholder="Add details...">Developed web features...</div>
                                <button type="button" class="btn-delete-bullet" title="Remove Bullet">×</button>
                            </li>
                        </ul>
                        <button type="button" class="btn-add-bullet">+ Add Bullet</button>
                    </div>
                    <div class="editor-entry-footer">
                        <span class="footer-label">Technologies: </span>
                        <span class="exp-technologies" contenteditable="true" placeholder="e.g. React, Python">Python</span>
                    </div>
                `;
                list.insertBefore(entry, list.firstChild);
                entry.querySelector(".exp-role").focus();
                debouncedSaveDraft();
                refreshIcons();
            }
        });
    }

    const btnAddProj = document.getElementById("btn-editor-add-proj");
    if (btnAddProj) {
        btnAddProj.addEventListener("click", () => {
            const list = document.getElementById("editor-projects-list");
            if (list) {
                const entry = document.createElement("div");
                entry.className = "editor-proj-entry editor-entry";
                entry.innerHTML = `
                    <div class="editor-entry-row flex justify-between">
                        <div class="editor-entry-header-left">
                            <span class="proj-name font-bold" contenteditable="true" placeholder="Project Name">Personal Project</span>
                            <span class="editor-separator"> | </span>
                            <span class="proj-link italic" contenteditable="true" placeholder="Project Link">https://github.com/...</span>
                        </div>
                        <div class="editor-entry-header-right flex items-center gap-sm">
                            <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                        </div>
                    </div>
                    <div class="proj-description-container">
                        <div class="proj-description text-muted" contenteditable="true" placeholder="Project One-liner Description...">A cool project...</div>
                    </div>
                    <div class="editor-bullets-container">
                        <ul class="editor-bullets-list">
                            <li class="editor-bullet-item">
                                <span class="bullet-dot">•</span>
                                <div class="editor-bullet-content" contenteditable="true" placeholder="Add details...">Built using HTML/JS...</div>
                                <button type="button" class="btn-delete-bullet" title="Remove Bullet">×</button>
                            </li>
                        </ul>
                        <button type="button" class="btn-add-bullet">+ Add Bullet</button>
                    </div>
                    <div class="editor-entry-footer">
                        <span class="footer-label">Technologies: </span>
                        <span class="proj-technologies" contenteditable="true" placeholder="e.g. React, Node.js">JS</span>
                    </div>
                `;
                list.insertBefore(entry, list.firstChild);
                entry.querySelector(".proj-name").focus();
                debouncedSaveDraft();
                refreshIcons();
            }
        });
    }

    const btnAddSkillCat = document.getElementById("btn-editor-add-skill-cat");
    if (btnAddSkillCat) {
        btnAddSkillCat.addEventListener("click", () => {
            const list = document.getElementById("editor-skills-categories");
            if (list) {
                const entry = document.createElement("div");
                entry.className = "editor-skill-category editor-entry flex";
                entry.innerHTML = `
                    <span class="skill-category-name font-bold" contenteditable="true" placeholder="Category Name">New Category</span>
                    <span class="editor-separator">: </span>
                    <span class="skill-category-skills flex-grow" contenteditable="true" placeholder="Skills (comma separated)">Skill 1, Skill 2</span>
                    <button type="button" class="btn-delete-entry" title="Remove Category"><i data-lucide="trash-2"></i></button>
                `;
                list.appendChild(entry);
                entry.querySelector(".skill-category-name").focus();
                debouncedSaveDraft();
                refreshIcons();
            }
        });
    }

    const btnAddEdu = document.getElementById("btn-editor-add-edu");
    if (btnAddEdu) {
        btnAddEdu.addEventListener("click", () => {
            const list = document.getElementById("editor-education-list");
            if (list) {
                const entry = document.createElement("div");
                entry.className = "editor-edu-entry editor-entry";
                entry.innerHTML = `
                    <div class="editor-entry-row flex justify-between">
                        <div class="editor-entry-header-left">
                            <span class="edu-institution font-bold" contenteditable="true" placeholder="Institution">University Name</span>
                            <span class="editor-separator"> — </span>
                            <span class="edu-degree font-semibold" contenteditable="true" placeholder="Degree">B.S. CS</span>
                        </div>
                        <div class="editor-entry-header-right flex items-center gap-sm">
                            <span class="edu-start-date" contenteditable="true" placeholder="Start Date">2020</span>
                            <span class="editor-separator"> — </span>
                            <span class="edu-end-date" contenteditable="true" placeholder="End Date">2024</span>
                            <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                        </div>
                    </div>
                    <div class="editor-entry-details flex gap-md">
                        <div>
                            <span class="footer-label">GPA: </span>
                            <span class="edu-gpa" contenteditable="true" placeholder="Omit or e.g. 3.9">3.9</span>
                        </div>
                        <div class="flex-grow">
                            <span class="footer-label">Coursework: </span>
                            <span class="edu-coursework" contenteditable="true" placeholder="e.g. Algorithms, Databases">Data Structures</span>
                        </div>
                    </div>
                `;
                list.appendChild(entry);
                entry.querySelector(".edu-institution").focus();
                debouncedSaveDraft();
                refreshIcons();
            }
        });
    }

    const btnAddCert = document.getElementById("btn-editor-add-cert");
    if (btnAddCert) {
        btnAddCert.addEventListener("click", () => {
            const list = document.getElementById("editor-certifications-list");
            if (list) {
                const entry = document.createElement("div");
                entry.className = "editor-cert-entry editor-entry";
                entry.innerHTML = `
                    <div class="editor-entry-row flex justify-between">
                        <div class="editor-entry-header-left">
                            <span class="cert-name font-bold" contenteditable="true" placeholder="Certification Name">AWS Certified</span>
                            <span class="editor-separator"> by </span>
                            <span class="cert-issuer font-semibold" contenteditable="true" placeholder="Issuer">Amazon</span>
                        </div>
                        <div class="editor-entry-header-right flex items-center gap-sm">
                            <span class="cert-date" contenteditable="true" placeholder="Date">2024</span>
                            <button type="button" class="btn-delete-entry" title="Remove Entry"><i data-lucide="trash-2"></i></button>
                        </div>
                    </div>
                    <div class="cert-description text-muted" contenteditable="true" placeholder="Optional description...">Certified...</div>
                `;
                list.appendChild(entry);
                entry.querySelector(".cert-name").focus();
                debouncedSaveDraft();
                refreshIcons();
            }
        });
    }
}

function collectEditorData() {
    const profile = {
        metadata: {
            version: 1
        },
        personal_info: {
            name: "",
            email: "",
            phone: null,
            links: {}
        },
        education: [],
        experience: [],
        projects: [],
        skills: {
            categories: {}
        },
        certifications: [],
        achievements: ""
    };

    const nameEl = document.getElementById("editor-pi-name");
    const emailEl = document.getElementById("editor-pi-email");
    const phoneEl = document.getElementById("editor-pi-phone");
    
    if (nameEl) profile.personal_info.name = nameEl.textContent.trim();
    if (emailEl) profile.personal_info.email = emailEl.textContent.trim();
    if (phoneEl) profile.personal_info.phone = phoneEl.textContent.trim() || null;
    
    const linkItems = document.querySelectorAll(".editor-link-item");
    linkItems.forEach(item => {
        const label = item.querySelector(".link-label").textContent.trim();
        const url = item.querySelector(".link-url").textContent.trim();
        if (label && url) {
            profile.personal_info.links[label] = url;
        }
    });

    const eduEntries = document.querySelectorAll(".editor-edu-entry");
    eduEntries.forEach(entry => {
        const institution = entry.querySelector(".edu-institution").textContent.trim();
        const degree = entry.querySelector(".edu-degree").textContent.trim();
        const startDate = entry.querySelector(".edu-start-date").textContent.trim();
        const endDate = entry.querySelector(".edu-end-date").textContent.trim();
        const gpaText = entry.querySelector(".edu-gpa")?.textContent.trim() || "";
        const courseworkText = entry.querySelector(".edu-coursework")?.textContent.trim() || "";
        
        if (institution && degree) {
            profile.education.push({
                institution: institution,
                degree: degree,
                start_date: startDate,
                end_date: endDate,
                gpa: gpaText ? parseFloat(gpaText) : null,
                coursework: courseworkText ? courseworkText.split(",").map(s => s.trim()).filter(Boolean) : []
            });
        }
    });

    const expEntries = document.querySelectorAll(".editor-exp-entry");
    expEntries.forEach(entry => {
        const company = entry.querySelector(".exp-company").textContent.trim();
        const role = entry.querySelector(".exp-role").textContent.trim();
        const startDate = entry.querySelector(".exp-start-date").textContent.trim();
        const endDate = entry.querySelector(".exp-end-date").textContent.trim();
        const workMode = entry.querySelector(".exp-work-mode")?.textContent.trim() || null;
        const techText = entry.querySelector(".exp-technologies")?.textContent.trim() || "";
        
        const bullets = [];
        entry.querySelectorAll(".editor-bullet-content").forEach(b => {
            const txt = b.textContent.trim();
            if (txt) bullets.push(txt);
        });

        if (company && role) {
            profile.experience.push({
                company: company,
                role: role,
                start_date: startDate,
                end_date: endDate,
                work_mode: workMode || null,
                bullets: bullets,
                list_type: "bullet",
                technologies: techText ? techText.split(",").map(s => s.trim()).filter(Boolean) : []
            });
        }
    });

    const projEntries = document.querySelectorAll(".editor-proj-entry");
    projEntries.forEach(entry => {
        const name = entry.querySelector(".proj-name").textContent.trim();
        const description = entry.querySelector(".proj-description")?.textContent.trim() || "";
        const link = entry.querySelector(".proj-link")?.textContent.trim() || null;
        const techText = entry.querySelector(".proj-technologies")?.textContent.trim() || "";
        
        const bullets = [];
        entry.querySelectorAll(".editor-bullet-content").forEach(b => {
            const txt = b.textContent.trim();
            if (txt) bullets.push(txt);
        });

        if (name) {
            profile.projects.push({
                name: name,
                description: description,
                bullets: bullets,
                list_type: "bullet",
                technologies: techText ? techText.split(",").map(s => s.trim()).filter(Boolean) : [],
                link: link || null
            });
        }
    });

    const skillCategories = document.querySelectorAll(".editor-skill-category");
    skillCategories.forEach(cat => {
        const catName = cat.querySelector(".skill-category-name").textContent.trim();
        const skillsText = cat.querySelector(".skill-category-skills").textContent.trim();
        if (catName) {
            profile.skills.categories[catName] = skillsText ? skillsText.split(",").map(s => s.trim()).filter(Boolean) : [];
        }
    });

    const certEntries = document.querySelectorAll(".editor-cert-entry");
    certEntries.forEach(entry => {
        const name = entry.querySelector(".cert-name").textContent.trim();
        const issuer = entry.querySelector(".cert-issuer").textContent.trim();
        const date = entry.querySelector(".cert-date").textContent.trim();
        const description = entry.querySelector(".cert-description")?.textContent.trim() || "";
        
        if (name && issuer) {
            profile.certifications.push({
                name: name,
                issuer: issuer,
                date: date,
                description: description
            });
        }
    });

    const achText = document.getElementById("editor-achievements-text");
    if (achText) {
        profile.achievements = achText.innerHTML.trim();
    }

    return profile;
}

// ── Autosave status indicator ─────────────────────────────────
// The workspace bottom bar used to show a static "All changes are saved
// automatically" message regardless of what actually happened — a failed
// save (e.g. a dropped connection) was only ever logged to the console,
// never surfaced to the user. setSaveStatus() drives the real indicator.
function setSaveStatus(state) {
    const container = document.getElementById("ws-save-status");
    const text = document.getElementById("ws-save-status-text");
    if (!container || !text) return;
    const icon = container.querySelector(".save-status-icon");

    container.classList.remove("is-saving", "is-error");

    if (state === "saving") {
        container.classList.add("is-saving");
        if (icon) icon.setAttribute("data-lucide", "loader");
        text.textContent = "Saving…";
    } else if (state === "error") {
        container.classList.add("is-error");
        if (icon) icon.setAttribute("data-lucide", "alert-circle");
        text.textContent = "Couldn't save — changes may be lost";
    } else {
        if (icon) icon.setAttribute("data-lucide", "check-circle");
        text.textContent = "All changes are saved automatically";
    }
    refreshIcons();
}

function debouncedSaveDraft() {
    clearTimeout(autoSaveTimeout);
    setSaveStatus("saving");
    autoSaveTimeout = setTimeout(() => {
        saveDraftToBackend();
    }, 1500);
}

async function saveDraftToBackend() {
    if (!workspaceSessionId) return;
    const profile = collectEditorData();
    try {
        const response = await fetch(`/api/tailor/draft/${workspaceSessionId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile })
        });
        if (response.ok) {
            setSaveStatus("saved");
        } else {
            console.warn("Working draft auto-save failed.");
            setSaveStatus("error");
        }
    } catch (err) {
        console.error("Auto-save network error:", err.message);
        setSaveStatus("error");
    }
}

async function forceSaveDraft() {
    clearTimeout(autoSaveTimeout);
    if (!workspaceSessionId) return;

    setSaveStatus("saving");
    const profile = collectEditorData();
    try {
        const response = await fetch(`/api/tailor/draft/${workspaceSessionId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile })
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.error || "Failed to save draft changes.");
        }
        setSaveStatus("saved");
    } catch (err) {
        setSaveStatus("error");
        throw err;
    }
}

function setupWorkspaceEventsOnce() {
    if (isEventsWired) return;
    
    // Refine with AI Chat Form
    const chatForm = document.getElementById("ws-chat-form");
    if (chatForm) {
        chatForm.addEventListener("submit", handleChatSubmit);
    }
    
    // Prompt Chips click handlers
    document.querySelectorAll(".prompt-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            const prompt = chip.dataset.prompt;
            const input = document.getElementById("ws-chat-input");
            if (input) {
                input.value = prompt;
                if (input.tagName === "TEXTAREA") {
                    input.style.height = "auto";
                    input.style.height = input.scrollHeight + "px";
                }
                const charCounter = document.getElementById("chat-char-counter");
                if (charCounter) {
                    charCounter.textContent = `${prompt.length}/500`;
                }
                handleChatSubmit();
            }
        });
    });

    // Auto-resize, Enter-submit, and character counter for chat textarea
    const chatInput = document.getElementById("ws-chat-input");
    const charCounter = document.getElementById("chat-char-counter");
    if (chatInput && chatInput.tagName === "TEXTAREA") {
        chatInput.addEventListener("input", function () {
            this.style.height = "auto";
            this.style.height = this.scrollHeight + "px";
            if (charCounter) {
                charCounter.textContent = `${this.value.length}/500`;
            }
        });
        chatInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                const sendBtn = document.getElementById("ws-chat-send");
                if (sendBtn) sendBtn.click();
            }
        });
    }
    
    // Start Over
    const btnStartOver = document.getElementById("ws-btn-startover");
    if (btnStartOver) {
        btnStartOver.addEventListener("click", async (e) => {
            const confirmed = await showConfirmModal({
                title: "Start Over?",
                message: "Are you sure you want to start over? Unsaved draft changes will be deleted.",
                confirmText: "Start Over",
            });
            if (confirmed) {
                setWorkspaceSessionId(null);
                window.location.hash = "#tailor";
            }
        });
    }
    
    // Download PDF
    const btnDownload = document.getElementById("ws-btn-download");
    if (btnDownload) {
        btnDownload.addEventListener("click", async () => {
            btnDownload.disabled = true;
            const originalHtml = btnDownload.innerHTML;
            btnDownload.innerHTML = `<span class="loading-spinner" style="width: 12px; height: 12px; border-width: 2px; margin: 0; display: inline-block; vertical-align: middle;"></span> Compiling...`;
            
            try {
                // Synchronously save any unsaved manual edits first
                await forceSaveDraft();
                
                // Fetch the PDF using a POST request
                const response = await fetch(`/api/tailor/download/${workspaceSessionId}`, {
                    method: "POST"
                });
                
                if (!response.ok) {
                    // Try to read the error message as JSON
                    let errorMsg = "Failed to download resume.";
                    try {
                        const errData = await response.json();
                        errorMsg = errData.error || errorMsg;
                    } catch (e) {
                        // Fallback to plain text if not JSON
                        const text = await response.text();
                        if (text) errorMsg = text;
                    }
                    throw new Error(errorMsg);
                }
                
                // Treat the response as a binary Blob
                const blob = await response.blob();
                
                // Create a File object so the browser has explicit filename metadata
                const filename = `BuildR_Tailored_${workspaceSessionId}.pdf`;
                const file = new File([blob], filename, { type: "application/pdf" });
                
                // Create an object URL from the File
                const url = window.URL.createObjectURL(file);
                
                // Trigger the download with the .pdf extension
                const a = document.createElement("a");
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                
                // Clean up the object URL after a short delay to give the browser time to initiate the download
                setTimeout(() => {
                    window.URL.revokeObjectURL(url);
                }, 2000);
                
                showToast("Download started!", "success");
            } catch (err) {
                showToast(err.message, "error");
            } finally {
                btnDownload.disabled = false;
                btnDownload.innerHTML = originalHtml;
                refreshIcons();
            }
        });
    }
    
    isEventsWired = true;
}



