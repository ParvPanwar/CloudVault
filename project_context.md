# Project Context: CloudVault

**Course:** UCS531: Cloud Computing  
**Project Title:** CloudVault: Decoupled Multi-Tenant Cloud Storage with In-Line Deduplication and Asynchronous Processing Using AWS S3, SQS, and EC2  
**Target Platform:** Amazon Web Services (AWS) — 100% Free Tier ($0.00 Incurred Cost)

---

## 1. Executive Summary

**CloudVault** is a cloud-native, multi-tenant document and media storage platform designed to solve two widespread problems in enterprise and educational cloud storage: **redundant storage consumption** and **synchronous compute bottlenecks**.

By combining **client-side cryptographic fingerprinting (SHA-256)** with an **asynchronous queue-worker pipeline (AWS SQS + EC2)** and **direct object storage (AWS S3 Pre-Signed URLs)**, CloudVault demonstrates how real-world cloud architectures achieve cost reduction, fault tolerance, and zero-trust security without exceeding free-tier resource boundaries.

---

## 2. Real-World Problem Statement

1. **Storage Redundancy & Cost Inefficiency:**
   * In multi-tenant environments (e.g., college portals, company file repositories), multiple users frequently upload duplicate copies of identical files (syllabus PDFs, lecture slides, software binaries, templates).
   * Conventional cloud setups store redundant copies blindly in object storage, driving up AWS S3 storage bills and wasting internet bandwidth.

2. **Synchronous Server Saturation:**
   * When applications perform file processing tasks (generating image thumbnails, watermarking confidential PDFs, or compression) synchronously within the web request cycle, web servers experience thread starvation, high latency, and request timeouts during peak submission hours.

3. **Insecure & Inefficient File Streaming:**
   * Routing large file streams directly through application servers consumes CPU and network capacity that should be dedicated to servicing lightweight API requests.

---

## 3. System Architecture & Core Innovation

CloudVault resolves these issues using a three-tiered architectural pipeline:

```
[ User Browser / Client ]
           │
           ├─ 1. Computes SHA-256 Hash of file locally
           ▼
[ REST API (FastAPI on EC2) ] ── (Queries Hash) ──► [ Amazon DynamoDB (NoSQL) ]
           │
           ├──► [IF HASH EXISTS - DUPLICATE]:
           │      • Skips S3 upload entirely.
           │      • Creates instant pointer in DynamoDB linked to current user.
           │      • Result: 0.1s completion, 0 extra bytes stored in S3.
           │
           └──► [IF HASH IS NEW]:
                  │
                  ├─ 2. Generates time-limited S3 Pre-Signed PUT URL.
                  │     (Browser streams file directly to raw-S3 bucket).
                  │
                  ├─ 3. Pushes processing job metadata to Amazon SQS Queue.
                  ▼
          [ Amazon SQS Queue ]
                  │
                  ├─ 4. Long-polled by Background Worker
                  ▼
          [ EC2 Worker Daemon ]
                  │
                  ├─ 5. Downloads raw file & executes compute:
                  │     - Watermarking ("CloudVault • Student Verified")
                  │     - Thumbnail generation / PDF compression
                  ├─ 6. Uploads output to processed-S3 bucket.
                  └─ 7. Marks file status as "READY" in DynamoDB.
```

---

## 4. Key Architectural Pillars

### A. In-Line Client-Side Deduplication (Cost Management)
* **How it works:** When a user selects a file, the browser calculates its SHA-256 hash in $\approx 20\text{ ms}$ using the native Web Crypto API.
* **Storage Optimization:** If the hash already exists in DynamoDB, no data is transferred to S3. A virtual reference is attached to the user's account.
* **Impact:** Demonstrates tangible cloud cost optimization with a real-time metrics dashboard showing logical storage vs. physical S3 storage used.

### B. Direct S3 Upload via IAM Pre-Signed URLs (Cloud Security)
* Application servers never handle binary file payloads directly.
* The API uses AWS IAM credentials to generate cryptographic, time-limited Pre-Signed URLs (valid for 5 minutes). The browser uploads directly to S3, protecting backend compute resources from bandwidth saturation.

### C. Decoupled Asynchronous Processing (SQS & EC2)
* Heavy tasks (watermarking, compression) are completely decoupled from user-facing APIs.
* If 1,000 files are uploaded at once, the API accepts them immediately, while jobs safely buffer inside AWS SQS. Worker instances consume jobs sequentially without server crashes or dropped requests.

### D. Secure Multi-Tenancy & Expiring Access
* Files are strictly isolated per user account.
* When downloading, the system generates temporary Pre-Signed GET links (valid for 60 seconds), ensuring zero unauthorized public access to the S3 bucket.

---

## 5. Mapping to UCS531 Syllabus Topics

| Syllabus Module | Project Implementation in CloudVault |
| :--- | :--- |
| **Amazon EC2 (Elastic Compute)** | Hosts the FastAPI REST gateway and runs the background Python worker daemon. |
| **Amazon S3 (Simple Storage Service)** | Segregated object storage (`raw-uploads` bucket and `processed` bucket) with lifecycle policies. |
| **Amazon SQS (Messaging in the Cloud)** | Message queuing service buffering incoming processing jobs to ensure system decoupling. |
| **AWS IAM & Security** | Least-privilege IAM roles and policies, temporary token generation, and tenant isolation. |
| **NoSQL Database (DynamoDB)** | Low-latency $O(1)$ hash collision indexing and multi-tenant document metadata management. |
| **Cost Management & QoS** | Real-time tracking of deduplication ratios and strict adherence to \$0.00 cloud spending. |

---

## 6. Financial Feasibility & Free-Tier Verification

CloudVault is engineered to guarantee **zero financial expense ($0.00)** by using only free tier services:

* **AWS SQS:** 1,000,000 requests per month (**Always Free**).
* **Amazon DynamoDB:** 25 GB storage and 25 RCU/WCU (**Always Free**).
* **Amazon S3:** Up to 5 GB standard storage (**12 Months Free**; testing uses $< 50\text{ MB}$).
* **Amazon EC2:** 750 hours/month of `t2.micro` / `t3.micro` (**12 Months Free**; covers 24/7 continuous uptime).
* **Hidden Cost Avoidance:** The project explicitly avoids paid services like Application Load Balancers (ALBs), NAT Gateways, and AWS EMR.

---

## 7. Live Viva Demonstration Strategy

During laboratory and final project evaluation, the live demo proceeds through three high-impact steps:

1. **Deduplication Demonstration:**
   * **Student A** uploads a 20 MB document $\rightarrow$ Takes 2–3 seconds; creates an object in S3.
   * **Student B** uploads the exact same document $\rightarrow$ Completes **instantly in 0.1 seconds**. The UI confirms: *"Duplicate hash detected; 0 bytes added to S3 storage."*
2. **Decoupled SQS Message Flow:**
   * Open the AWS SQS Management Console on screen.
   * Upload a fresh image and show the queue depth increase to `1`.
   * Show the EC2 terminal logs as the worker picks up the message, applies the watermark, uploads to the processed bucket, and acknowledges the queue.
3. **IAM Security & Expiring Links:**
   * Click "Download" to generate an expiring S3 link.
   * Demonstrate successful download, then attempt access after the link expires to show AWS returning `AccessDenied`.
