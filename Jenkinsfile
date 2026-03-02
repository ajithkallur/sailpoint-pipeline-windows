// ============================================================
// Jenkinsfile — SailPoint ISC CI/CD Pipeline (Windows Edition)
// ============================================================
// Runs on a Windows Jenkins agent using bat() instead of sh().
// Jenkins simply calls: python pipeline.py <command>
//
// PREREQUISITES ON THE WINDOWS JENKINS MACHINE:
//   1. Jenkins installed as a Windows service
//   2. Python 3.6+ installed and added to PATH
//      Download: https://www.python.org/downloads/windows/
//      IMPORTANT: During install, tick "Add Python to PATH"
//   3. Git installed: https://git-scm.com/download/win
//
// JENKINS CREDENTIALS TO CREATE FIRST:
//   Manage Jenkins > Credentials > System > Global > Add Credential
//   Type = "Secret text" for each one. IDs must match EXACTLY:
//     SOURCE_CLIENT_ID      SOURCE_CLIENT_SECRET
//     DEV_CLIENT_ID         DEV_CLIENT_SECRET
//     PROD_CLIENT_ID        PROD_CLIENT_SECRET
//     SLACK_WEBHOOK_URL     (create even if unused — put a single space as the value)
// ============================================================

pipeline {
    agent any

    environment {
        // ── Update these three URLs to match your real SailPoint tenants ──
        SOURCE_TENANT_URL = 'https://partner5434.api.identitynow-demo.com'
        DEV_TENANT_URL    = 'https://partner5434.api.identitynow-demo.com'
        PROD_TENANT_URL   = 'https://partner5434.api.identitynow-demo.com'

        CONFIG_FILE = 'config-export.json'
        BACKUP_DIR  = 'backups'
    }

    triggers {
        // Auto-trigger on every GitHub push
        // Requires a GitHub webhook pointing to your Jenkins URL
        githubPush()
    }

    options {
        // 26 hours total: 24h approval window + 2h for actual pipeline work
        timeout(time: 26, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '10'))
        timestamps()
    }

    stages {

        // ── Stage 1: Checkout ────────────────────────────────────────
        stage('Checkout') {
            steps {
                echo '--- Pulling latest code from GitHub ---'
                checkout scm
                echo "Branch: ${env.GIT_BRANCH}"
                echo "Commit: ${env.GIT_COMMIT}"
            }
        }

        // ── Stage 2: Verify Python ───────────────────────────────────
        // Confirms Python is installed and on the PATH before doing anything else.
        // If this stage fails: open a Windows Command Prompt, run "python --version".
        // If that also fails, Python is not on your PATH — reinstall Python and
        // tick the "Add Python to PATH" checkbox during installation.
        stage('Verify Python') {
            steps {
                bat 'python --version'
                bat 'python -c "import urllib.request, json, os, sys; print(\'All required modules OK\')"'
            }
        }

        // ── Stage 3: Export ──────────────────────────────────────────
        // Connects to source ISC tenant, exports config to config-export.json
        stage('Export from Source Tenant') {
            steps {
                withCredentials([
                    string(credentialsId: 'SOURCE_CLIENT_ID',     variable: 'SOURCE_CLIENT_ID'),
                    string(credentialsId: 'SOURCE_CLIENT_SECRET',  variable: 'SOURCE_CLIENT_SECRET'),
                    string(credentialsId: 'DEV_CLIENT_ID',        variable: 'DEV_CLIENT_ID'),
                    string(credentialsId: 'DEV_CLIENT_SECRET',    variable: 'DEV_CLIENT_SECRET'),
                    string(credentialsId: 'PROD_CLIENT_ID',       variable: 'PROD_CLIENT_ID'),
                    string(credentialsId: 'PROD_CLIENT_SECRET',   variable: 'PROD_CLIENT_SECRET'),
                    string(credentialsId: 'SLACK_WEBHOOK_URL',    variable: 'SLACK_WEBHOOK_URL')
                ]) {
                    bat 'python pipeline.py export'
                }
                archiveArtifacts artifacts: 'config-export.json', fingerprint: true
            }
        }

        // ── Stage 4: Validate ────────────────────────────────────────
        // Checks the exported JSON is valid. Pipeline stops here if broken.
        stage('Validate Config') {
            steps {
                withCredentials([
                    string(credentialsId: 'SOURCE_CLIENT_ID',     variable: 'SOURCE_CLIENT_ID'),
                    string(credentialsId: 'SOURCE_CLIENT_SECRET',  variable: 'SOURCE_CLIENT_SECRET'),
                    string(credentialsId: 'DEV_CLIENT_ID',        variable: 'DEV_CLIENT_ID'),
                    string(credentialsId: 'DEV_CLIENT_SECRET',    variable: 'DEV_CLIENT_SECRET'),
                    string(credentialsId: 'PROD_CLIENT_ID',       variable: 'PROD_CLIENT_ID'),
                    string(credentialsId: 'PROD_CLIENT_SECRET',   variable: 'PROD_CLIENT_SECRET'),
                    string(credentialsId: 'SLACK_WEBHOOK_URL',    variable: 'SLACK_WEBHOOK_URL')
                ]) {
                    bat 'python pipeline.py validate'
                }
            }
        }

        // ── Stage 5: Deploy to Dev ───────────────────────────────────
        // Backs up dev tenant then imports config. Auto-rolls back on failure.
        stage('Deploy to Dev') {
            steps {
                withCredentials([
                    string(credentialsId: 'SOURCE_CLIENT_ID',     variable: 'SOURCE_CLIENT_ID'),
                    string(credentialsId: 'SOURCE_CLIENT_SECRET',  variable: 'SOURCE_CLIENT_SECRET'),
                    string(credentialsId: 'DEV_CLIENT_ID',        variable: 'DEV_CLIENT_ID'),
                    string(credentialsId: 'DEV_CLIENT_SECRET',    variable: 'DEV_CLIENT_SECRET'),
                    string(credentialsId: 'PROD_CLIENT_ID',       variable: 'PROD_CLIENT_ID'),
                    string(credentialsId: 'PROD_CLIENT_SECRET',   variable: 'PROD_CLIENT_SECRET'),
                    string(credentialsId: 'SLACK_WEBHOOK_URL',    variable: 'SLACK_WEBHOOK_URL')
                ]) {
                    bat 'python pipeline.py deploy-dev'
                }
            }
        }

        // ── Stage 6: Manual Approval ─────────────────────────────────
        // Pipeline PAUSES here. Log into dev tenant, verify the changes look
        // correct, then come back to Jenkins and click the Approve button.
        // Automatically aborts after 24 hours if nobody approves (safe — prod untouched).
        stage('Approve Production Deploy') {
            steps {
                script {
                    def approver = input(
                        message: 'Dev looks good? Ready to deploy to PRODUCTION?',
                        ok: 'Yes, deploy to Production',
                        submitterParameter: 'APPROVED_BY'
                    )
                    echo "Production deployment approved by: ${approver}"
                }
            }
        }

        // ── Stage 7: Deploy to Production ───────────────────────────
        // Only runs after manual approval. Backs up prod first.
        // Auto-rolls back if anything fails.
        stage('Deploy to Production') {
            steps {
                withCredentials([
                    string(credentialsId: 'SOURCE_CLIENT_ID',     variable: 'SOURCE_CLIENT_ID'),
                    string(credentialsId: 'SOURCE_CLIENT_SECRET',  variable: 'SOURCE_CLIENT_SECRET'),
                    string(credentialsId: 'DEV_CLIENT_ID',        variable: 'DEV_CLIENT_ID'),
                    string(credentialsId: 'DEV_CLIENT_SECRET',    variable: 'DEV_CLIENT_SECRET'),
                    string(credentialsId: 'PROD_CLIENT_ID',       variable: 'PROD_CLIENT_ID'),
                    string(credentialsId: 'PROD_CLIENT_SECRET',   variable: 'PROD_CLIENT_SECRET'),
                    string(credentialsId: 'SLACK_WEBHOOK_URL',    variable: 'SLACK_WEBHOOK_URL')
                ]) {
                    bat 'python pipeline.py deploy-prod'
                }
            }
        }
    }

    post {
        success {
            echo 'Pipeline completed successfully!'
        }
        failure {
            echo 'Pipeline FAILED. Check the console output above for the exact error.'
        }
        aborted {
            echo 'Pipeline aborted. If this was the approval step, prod was NOT changed.'
        }
        always {
            archiveArtifacts artifacts: 'backups/*.json', allowEmptyArchive: true
            cleanWs()
        }
    }
}
