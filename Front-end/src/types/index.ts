// ============================================================
// PaySentinelIQ — Core TypeScript Domain Types
// ============================================================

// ── Tenant / Company ── //

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  logo_url: string | null;
  plan: "starter" | "professional" | "enterprise";
  features: TenantFeature[];
  created_at: string;
}

export type TenantFeature =
  | "fraud_detection"
  | "compliance_intelligence"
  | "payroll_generation"
  | "document_analysis"
  | "audit_logs"
  | "multi_user"
  | "api_access";

// ── User / Auth ── //

export interface User {
  id: string;
  email: string;
  full_name: string;
  avatar_url: string | null;
  role: UserRole;
  tenant_id: string;
  tenant?: Tenant;
  mfa_enabled: boolean;
  last_login: string | null;
  created_at: string;
}

export type UserRole =
  | "admin"
  | "fraud_analyst"
  | "compliance_officer"
  | "hr_manager"
  | "payroll_specialist"
  | "auditor"
  | "viewer";

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

// ── Payroll ── //

export interface Payroll {
  id: string;
  tenant_id: string;
  employee_id: string;
  employee_name: string;
  period_start: string;
  period_end: string;
  gross_pay: number;
  net_pay: number;
  tax_withheld: number;
  deductions: PayrollDeduction[];
  status: PayrollStatus;
  risk_score: number;
  verified_by_ai: boolean;
  created_at: string;
  updated_at: string;
}

export interface PayrollDeduction {
  type: string;
  amount: number;
  description: string;
}

export type PayrollStatus =
  | "draft"
  | "pending_verification"
  | "verified"
  | "flagged"
  | "approved"
  | "rejected";

// ── Fraud / Risk ── //

export interface FraudAlert {
  id: string;
  tenant_id: string;
  document_id: string;
  document_type: DocumentType;
  risk_level: RiskLevel;
  risk_score: number;
  ai_confidence: number;
  anomaly_category: AnomalyCategory;
  description: string;
  ai_explanation: string;
  flagged_fields: FlaggedField[];
  status: FraudAlertStatus;
  assigned_to: string | null;
  created_at: string;
  resolved_at: string | null;
}

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type AnomalyCategory =
  | "salary_discrepancy"
  | "duplicate_payment"
  | "ghost_employee"
  | "tax_evasion"
  | "timesheet_fraud"
  | "unauthorized_deduction"
  | "identity_mismatch"
  | "document_forgery"
  | "compliance_violation"
  | "other";

export type FraudAlertStatus =
  | "new"
  | "under_review"
  | "escalated"
  | "confirmed_fraud"
  | "false_positive"
  | "resolved";

export interface FlaggedField {
  field_name: string;
  expected_value: string;
  detected_value: string;
  confidence: number;
  explanation: string;
}

export type DocumentType =
  | "payroll_report"
  | "tax_form"
  | "timesheet"
  | "employment_contract"
  | "bank_statement"
  | "identity_document"
  | "compliance_report";

// ── Verification ── //

export interface VerificationResult {
  id: string;
  document_id: string;
  status: VerificationStatus;
  risk_score: number;
  extracted_fields: Record<string, string>;
  metadata_analysis: MetadataAnalysis;
  fraud_indicators: FraudIndicator[];
  ocr_confidence: number;
  ai_explanation: string;
  verified_at: string | null;
}

export type VerificationStatus = "pending" | "in_progress" | "verified" | "failed" | "needs_review";

export interface MetadataAnalysis {
  file_type: string;
  file_size_bytes: number;
  created_date: string | null;
  modified_date: string | null;
  author: string | null;
  software: string | null;
  anomalies: string[];
}

export interface FraudIndicator {
  type: string;
  severity: RiskLevel;
  description: string;
  location?: { page: number; x: number; y: number; width: number; height: number };
}

// ── Compliance ── //

export interface ComplianceRecord {
  id: string;
  tenant_id: string;
  entity_name: string;
  entity_type: "company" | "employee" | "vendor";
  verification_status: "verified" | "unverified" | "flagged";
  risk_level: RiskLevel;
  public_records_summary: string;
  lawsuit_summary: string | null;
  sanctions_check: boolean;
  pep_check: boolean;
  adverse_media: string[];
  last_checked: string;
}

// ── AI Insights ── //

export interface AIInsight {
  id: string;
  tenant_id: string;
  title: string;
  summary: string;
  detailed_analysis: string;
  risk_score: number;
  confidence: number;
  category: AnomalyCategory;
  recommended_actions: string[];
  related_documents: string[];
  created_at: string;
}

// ── Audit Log ── //

export interface AuditLogEntry {
  id: string;
  tenant_id: string;
  user_id: string;
  user_name: string;
  action: AuditAction;
  entity_type: string;
  entity_id: string;
  details: Record<string, unknown>;
  ip_address: string;
  user_agent: string;
  created_at: string;
}

export type AuditAction =
  | "user.login"
  | "user.logout"
  | "user.mfa_verified"
  | "user.register"
  | "document.uploaded"
  | "document.verified"
  | "document.flagged"
  | "fraud.alert_created"
  | "fraud.alert_reviewed"
  | "fraud.alert_resolved"
  | "payroll.created"
  | "payroll.approved"
  | "compliance.checked"
  | "report.generated"
  | "report.viewed"
  | "settings.updated"
  // ── Event-driven audit trail (RabbitMQ consumers) ── //
  | "bank_slip.analysis.started"
  | "bank_slip.analysis.completed"
  | "bank_slip.analysis.failed"
  | "payroll.analysis.started"
  | "payroll.analysis.completed"
  | "payroll.analysis.failed"
  | "document.analysis.started"
  | "document.analysis.completed"
  | "document.analysis.failed"
  | "bill.scheduled"
  | "bill.cancelled"
  | "bill.paid"
  | "bill.due_soon"
  | "bill.overdue"
  | "notification.created"
  | "notification.read";

// ── Notification ── //

export interface Notification {
  id: string;
  user_id: string;
  tenant_id: string; // Added tenant_id for consistency
  type: "payment" | "fraud_alert" | "verification_complete" | "compliance_alert" | "document_event" | "system" | "ai_insight" | "critical";
  title: string;
  message: string;
  severity: "critical" | "warning" | "normal" | "success" | "ai";
  is_read: boolean;
  action_url: string | null;
  metadata: Record<string, any>; // Added metadata field
  created_at: string;
}

// ── Employee ── //

export interface Employee {
  id: string;
  tenant_id: string;
  full_name: string;
  email: string;
  department: string;
  position: string;
  salary: number;
  hire_date: string;
  status: "active" | "terminated" | "on_leave";
  risk_score: number;
}

// ── API Response Wrappers ── //

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface APIError {
  code: string;
  message: string;
  details?: Record<string, string[]>;
}
