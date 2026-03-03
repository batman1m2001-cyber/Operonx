//! In-memory job store for background job execution.

use std::sync::Mutex;
use std::collections::HashMap;

use chrono::{DateTime, Utc};
use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    Pending,
    Running,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Serialize)]
pub struct Job {
    pub id: String,
    pub status: JobStatus,
    pub result: Option<Value>,
    pub error: Option<String>,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

pub struct JobStore {
    jobs: Mutex<HashMap<String, Job>>,
}

impl JobStore {
    pub fn new() -> Self {
        JobStore {
            jobs: Mutex::new(HashMap::new()),
        }
    }

    pub fn create(&self, id: String) -> Job {
        let job = Job {
            id: id.clone(),
            status: JobStatus::Pending,
            result: None,
            error: None,
            created_at: Utc::now(),
            completed_at: None,
        };
        self.jobs.lock().unwrap().insert(id, job.clone());
        job
    }

    pub fn set_running(&self, id: &str) {
        if let Some(job) = self.jobs.lock().unwrap().get_mut(id) {
            job.status = JobStatus::Running;
        }
    }

    pub fn set_completed(&self, id: &str, result: Value) {
        if let Some(job) = self.jobs.lock().unwrap().get_mut(id) {
            job.status = JobStatus::Completed;
            job.result = Some(result);
            job.completed_at = Some(Utc::now());
        }
    }

    pub fn set_failed(&self, id: &str, error: String) {
        if let Some(job) = self.jobs.lock().unwrap().get_mut(id) {
            job.status = JobStatus::Failed;
            job.error = Some(error);
            job.completed_at = Some(Utc::now());
        }
    }

    pub fn get(&self, id: &str) -> Option<Job> {
        self.jobs.lock().unwrap().get(id).cloned()
    }

    /// Remove completed/failed jobs older than `ttl_seconds`.
    pub fn cleanup(&self, ttl_seconds: i64) {
        let cutoff = Utc::now() - chrono::Duration::seconds(ttl_seconds);
        self.jobs.lock().unwrap().retain(|_, job| {
            if matches!(job.status, JobStatus::Completed | JobStatus::Failed) {
                job.completed_at
                    .map(|t| t > cutoff)
                    .unwrap_or(true)
            } else {
                true
            }
        });
    }
}
