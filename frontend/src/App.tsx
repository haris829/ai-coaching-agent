import type { ReactNode } from 'react';
import { NavLink, Navigate, Route, Routes } from 'react-router-dom';

import { IdentitySwitcher } from './components/IdentitySwitcher';
import { ToastProvider } from './components/ui';
import { AnalyticsPage } from './pages/AnalyticsPage';
import { AttemptPage } from './pages/AttemptPage';
import { AttemptReportPage } from './pages/AttemptReportPage';
import { FormalAssessmentPage } from './pages/FormalAssessmentPage';
import { ImportPage } from './pages/ImportPage';
import { LearnerRulesPage } from './pages/LearnerRulesPage';
import { QuestionFormPage } from './pages/QuestionFormPage';
import { QuestionListPage } from './pages/QuestionListPage';
import { QuizConfigurationPage } from './pages/QuizConfigurationPage';
import { RetakePage } from './pages/RetakePage';
import { TopicsPage } from './pages/TopicsPage';

/**
 * Test UI for the Courses Quiz Agent backend.
 *
 * This is a **development and manual-verification surface**, not a production frontend: it exists so
 * the whole workflow can be exercised in a browser — configure a quiz, watch versions accumulate,
 * author and retire questions, import a CSV, read the learner rules, sit the quiz and watch it save
 * and submit, retake it, sit a supervised examination, and read the administrator's dashboard. The
 * backend is the product; every rule it enforces is enforced there, not here.
 *
 * The navigation follows the capabilities: configuration and rules are UC-01; questions, topics,
 * import and reports are UC-02; "Take a quiz" is UC-03 and its result chain is UC-04 to UC-07;
 * "Retakes" is UC-08; "Formal assessment" is UC-09 (and the assessor's review queue, which appears
 * on that screen when the assessor identity is selected); "Analytics" is UC-10.
 *
 * Screens are not access control. Role-specific panels are hidden as a courtesy — every endpoint
 * behind them enforces the role itself and refuses the wrong credential with 403.
 */
export function App(): ReactNode {
  return (
    <ToastProvider>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            Courses Quiz Agent <small>test UI</small>
          </div>
          <nav className="nav">
            <NavLink to="/configuration" className={({ isActive }) => (isActive ? 'active' : '')}>
              Configuration
            </NavLink>
            <NavLink to="/questions" className={({ isActive }) => (isActive ? 'active' : '')}>
              Questions
            </NavLink>
            <NavLink to="/topics" className={({ isActive }) => (isActive ? 'active' : '')}>
              Topics
            </NavLink>
            <NavLink to="/import" className={({ isActive }) => (isActive ? 'active' : '')}>
              CSV import
            </NavLink>
            <NavLink to="/rules" className={({ isActive }) => (isActive ? 'active' : '')}>
              Learner rules
            </NavLink>
            <NavLink to="/attempt" className={({ isActive }) => (isActive ? 'active' : '')}>
              Take a quiz
            </NavLink>
            <NavLink to="/reports" className={({ isActive }) => (isActive ? 'active' : '')}>
              Attempt reports
            </NavLink>
            <NavLink to="/retakes" className={({ isActive }) => (isActive ? 'active' : '')}>
              Retakes
            </NavLink>
            <NavLink to="/formal" className={({ isActive }) => (isActive ? 'active' : '')}>
              Formal assessment
            </NavLink>
            <NavLink to="/analytics" className={({ isActive }) => (isActive ? 'active' : '')}>
              Analytics
            </NavLink>
          </nav>
          <IdentitySwitcher />
        </header>

        <Routes>
          <Route path="/" element={<Navigate to="/configuration" replace />} />

          {/* UC-01 — Quiz Configuration & Rules */}
          <Route path="/configuration" element={<QuizConfigurationPage />} />
          <Route path="/rules" element={<LearnerRulesPage />} />

          {/* UC-02 — Question Bank Management */}
          <Route path="/questions" element={<QuestionListPage />} />
          <Route path="/questions/new" element={<QuestionFormPage />} />
          <Route path="/questions/:id" element={<QuestionFormPage />} />
          <Route path="/topics" element={<TopicsPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/reports" element={<AttemptReportPage />} />

          {/* UC-03 — Quiz Attempt Delivery */}
          <Route path="/attempt" element={<AttemptPage />} />
          <Route path="/reports/:attemptRef" element={<AttemptReportPage />} />

          {/* UC-08 — Retake Management (eligibility, attempt history, administrator grants) */}
          <Route path="/retakes" element={<RetakePage />} />

          {/* UC-09 — Formal Assessment Mode, and the assessor's review queue */}
          <Route path="/formal" element={<FormalAssessmentPage />} />

          {/* UC-10 — Analytics & Reporting */}
          <Route path="/analytics" element={<AnalyticsPage />} />

          <Route
            path="*"
            element={
              <div className="page">
                <div className="empty">
                  Page not found. <NavLink to="/configuration">Go to quiz configuration</NavLink>.
                </div>
              </div>
            }
          />
        </Routes>
      </div>
    </ToastProvider>
  );
}
