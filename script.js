// ================= LOGIN =================

function login() {

    const username = document.getElementById("username").value.trim().toLowerCase();
    const password = document.getElementById("password").value.trim();
    const role = document.getElementById("role").value.trim().toLowerCase();

    // Check empty fields
    if (username === "" || password === "") {
        alert("Please enter username and password.");
        return;
    }

    // ADMIN
    if (username === "admin" && password === "1234" && role === "admin") {
        alert("Admin Login Successful!");
        window.location.href = "dashboard.html";
        return;
    }

    // STUDENT
    if (username === "student" && password === "1234" && role === "student") {
        alert("Student Login Successful!");
        window.location.href = "dashboard.html";
        return;
    }

    // COMPANY
    if (username === "company" && password === "1234" && role === "company") {
        alert("Company Login Successful!");
        window.location.href = "dashboard.html";
        return;
    }

    // Wrong login
    alert("Invalid Login");
}


// ================= PAGE NAVIGATION =================

function showPage(pageName, event) {

    const pages = document.querySelectorAll(".page");

    pages.forEach(function(page) {
        page.classList.remove("active-page");
    });

    const selectedPage = document.getElementById(pageName);

    if (selectedPage) {
        selectedPage.classList.add("active-page");
    }

    const titles = {
        dashboard: "Dashboard",
        profile: "My Profile",
        jobs: "Job Opportunities",
        applications: "My Applications",
        resume: "AI Resume Analyzer",
        eligibility: "Eligibility Check",
        interviews: "Interviews",
        analytics: "Placement Analytics"
    };

    const title = document.getElementById("page-title");

    if (title && titles[pageName]) {
        title.innerText = titles[pageName];
    }

    const buttons = document.querySelectorAll(".nav-item");

    buttons.forEach(function(button) {
        button.classList.remove("active");
    });

    if (event && event.currentTarget) {
        event.currentTarget.classList.add("active");
    }
}


// ================= APPLY FOR JOB =================

function applyJob(button) {

    button.innerText = "Applied ✓";
    button.style.background = "#dcfce7";
    button.style.color = "#16a34a";
    button.disabled = true;

    alert("Application submitted successfully!");
}


// ================= AI RESUME ANALYZER =================

function analyzeResume() {

    const result = document.getElementById("resume-result");

    if (result) {
        result.style.display = "block";

        result.scrollIntoView({
            behavior: "smooth"
        });
    }
}


// ================= ELIGIBILITY CHECK =================

function checkEligibility() {

    const cgpa = parseFloat(
        document.getElementById("cgpa").value
    );

    const backlogs = parseInt(
        document.getElementById("backlogs").value
    );

    const result =
        document.getElementById("eligibility-result");

    if (cgpa >= 7 && backlogs === 0) {

        result.innerHTML = `
            <div style="
                background:#dcfce7;
                padding:20px;
                border-radius:10px;
                color:#166534;
            ">
                <h3>✓ Eligible</h3>

                <p>
                    AI analysis indicates that you are eligible
                    for most placement opportunities.
                </p>
            </div>
        `;

    } else {

        result.innerHTML = `
            <div style="
                background:#fee2e2;
                padding:20px;
                border-radius:10px;
                color:#991b1b;
            ">
                <h3>⚠ Not Eligible</h3>

                <p>
                    Your current academic profile does not meet
                    the selected eligibility criteria.
                </p>
            </div>
        `;
    }
}


// ================= LOGOUT =================
function logout() {
    alert("Logged out successfully.");
    window.location.href = "index.html";
}