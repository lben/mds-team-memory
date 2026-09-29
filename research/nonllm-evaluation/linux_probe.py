"""Offline RHEL userspace compatibility probe; not an accuracy benchmark."""
import json
import os
from pathlib import Path
import resource
import time

os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',
                  HF_HUB_DISABLE_TELEMETRY='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
import torch
from transformers import AutoModelForSequenceClassification,AutoTokenizer
import score

torch.set_num_threads(4)
torch.set_num_interop_threads(1)
directory=Path(__file__).resolve().parent
tokenizer=AutoTokenizer.from_pretrained(str(directory/'nli-model'),local_files_only=True,trust_remote_code=False)
model=AutoModelForSequenceClassification.from_pretrained(str(directory/'nli-model'),local_files_only=True,trust_remote_code=False).eval()
pairs=[('Prism Hopper routes queued requests and stores their delivery receipts.','Prism Hopper'),
       ('During lunch beside Prism Hopper, I reviewed receipt numbers.','Prism Hopper'),
       ('The timeout value is 30 seconds.','30 seconds'),
       ('Does Prism Hopper preserve a receipt after restart?','Prism Hopper')]
began=time.perf_counter()
with torch.inference_mode():
    tokens=tokenizer([p[0] for p in pairs],[score.NLI_HYPOTHESIS.format(name=p[1]) for p in pairs],
                     padding=True,truncation='only_first',max_length=512,return_tensors='pt')
    logits=model(**tokens).logits
    margins=logits[:,1]-torch.logsumexp(logits[:,[0,2]],dim=1)
assert torch.isfinite(margins).all()
print(json.dumps({'scope':'compatibility only; no gold accuracy claimed','pairs':pairs,
                  'margins':margins.tolist(),'seconds':time.perf_counter()-began,
                  'torch':torch.__version__,'cpu_only':not torch.cuda.is_available(),
                  'threads':torch.get_num_threads(),'peak_rss_platform_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},indent=2))
