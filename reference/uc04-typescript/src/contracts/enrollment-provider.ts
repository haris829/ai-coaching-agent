/**
 * PORT: Enrollment / access control.
 * TODAY  -> MockEnrollmentProvider
 * LATER  -> Company enrollment & access service
 *
 * UC-04 treats any failure here as "not verified" and FAILS CLOSED: lesson content is
 * never loaded or exposed unless this returns enrolled === true.
 */
export interface EnrollmentStatus {
  enrolled: boolean;
  /** Optional provider-side reason, surfaced only in logs. */
  reason?: string;
}

export interface EnrollmentProvider {
  isEnrolled(userId: string, courseId: string): Promise<EnrollmentStatus>;
}
