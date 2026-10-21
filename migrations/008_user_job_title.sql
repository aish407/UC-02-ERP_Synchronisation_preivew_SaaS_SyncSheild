UPDATE users SET job_title='Other' WHERE job_title IS NULL OR trim(job_title)='';
