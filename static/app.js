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
        content: initialHtml || "",
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
    const views = ["#view-home", "#view-builder", "#view-tailor", "#view-tailor-workspace"];
    
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

    // Load setup data if tailoring page is opened
    if (hash === "#tailor") {
        // Auto-load contact details from cached profile
        if (cachedProfile) {
            const pi = cachedProfile.personal_info || {};
            const nameField = document.getElementById("tailor-pi-name");
            const emailField = document.getElementById("tailor-pi-email");
            const phoneField = document.getElementById("tailor-pi-phone");
            
            if (nameField && !nameField.value.trim()) nameField.value = pi.name || "";
            if (emailField && !emailField.value.trim()) emailField.value = pi.email || "";
            if (phoneField && !phoneField.value.trim()) phoneField.value = pi.phone || "";
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
                if (state.contact_info) {
                    const c = state.contact_info;
                    if (c.name) document.getElementById("tailor-pi-name").value = c.name;
                    if (c.email) document.getElementById("tailor-pi-email").value = c.email;
                    if (c.phone) document.getElementById("tailor-pi-phone").value = c.phone;
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

// ── Update Master Resume Card UI ─────────────────────────────
function updateMasterResumeStatusCard(profile) {
    const card = document.getElementById("master-resume-card");
    if (!card) return;

    const statusText = document.getElementById("status-text");
    const statusIcon = document.getElementById("status-icon");
    const dateContainer = document.getElementById("status-date-container");
    const dateValue = document.getElementById("status-date-value");
    const statusDesc = document.getElementById("status-desc");
    const statusActionBtn = document.getElementById("status-action-btn");

    if (profile.has_valid_resume) {
        card.className = "status-card status-available";
        statusText.textContent = "Master Resume Available";
        statusText.style.color = "var(--success)";
        
        statusIcon.setAttribute("data-lucide", "check-circle");
        statusIcon.setAttribute("class", "status-icon-available");
        
        dateContainer.style.display = "block";
        if (profile.metadata && profile.metadata.updated_at) {
            try {
                const date = new Date(profile.metadata.updated_at);
                dateValue.textContent = date.toLocaleString();
            } catch (e) {
                dateValue.textContent = "Available";
            }
        } else {
            dateValue.textContent = "Available";
        }
        
        statusDesc.textContent = "Your Master Resume is fully configured and ready to use. You can generate a PDF copy, view its contents, or tailor it to custom jobs using our AI tools.";
        
        statusActionBtn.textContent = "Edit Master Resume";
        statusActionBtn.className = "btn-secondary";
        statusActionBtn.href = "#builder";
    } else {
        card.className = "status-card status-missing";
        statusText.textContent = "No Master Resume Found";
        statusText.style.color = "var(--warning)";
        
        statusIcon.setAttribute("data-lucide", "alert-circle");
        statusIcon.setAttribute("class", "status-icon-missing");
        
        dateContainer.style.display = "none";
        
        statusDesc.textContent = "Please create a Master Resume first. Your Master Resume serves as the raw source of truth for the AI to tailor resume variants.";
        
        statusActionBtn.textContent = "Create Master Resume";
        statusActionBtn.className = "btn-primary";
        statusActionBtn.href = "#builder";
    }
    refreshIcons();
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

        updateMasterResumeStatusCard(profile);
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

async function loadLibrary() {
    try {
        const response = await fetch("/api/resumes");
        const resumes = await response.json();

        // Handle error response
        if (resumes.error) {
            console.error("Failed to load library:", resumes.error);
            return;
        }

        // Clear current list
        libraryList.innerHTML = "";

        if (resumes.length === 0) {
            libraryEmptyState.style.display = "block";
            return;
        }

        libraryEmptyState.style.display = "none";

        resumes.forEach(resume => {
            libraryList.appendChild(renderLibraryItem(resume));
        });

        refreshIcons();
    } catch (err) {
        console.error("Failed to load resume library:", err);
    }
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

    const deleteBtn = item.querySelector(".btn-delete");
    deleteBtn.addEventListener("click", () => {
        deleteResumeFromLibrary(resume.id, item);
    });

    return item;
}

async function deleteResumeFromLibrary(resumeId, itemElement) {
    // ── Confirm before deleting ──────────────────────────────
    if (!confirm("Delete this resume? This cannot be undone.")) {
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


// ═══════════════════════════════════════════════════════════════
// 8. EVENT WIRING — connect buttons to functions
// ═══════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
    // ── Load profile on page load ────────────────────────────
    loadProfile();

    // ── Initial Icon Rendering ──────────────────────────────
    refreshIcons();

    // ── Theme Toggle Event Wiring ────────────────────────────
    const themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const currentTheme = document.documentElement.getAttribute("data-theme");
            const newTheme = currentTheme === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", newTheme);
            localStorage.setItem("theme", newTheme);
        });
    }

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
    document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {
            e.preventDefault();
            saveProfile();
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

