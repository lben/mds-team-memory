from pathlib import Path
import sys
sys.path.insert(0,str(Path.cwd()/"tools"))
import testdata,update_session
prompts=[]
def password(prompt):
    prompts.append(prompt)
    return "isolated-update-test"
update_session.getpass.getpass=password
code=testdata.main(sys.argv[1:])
print("PASSWORD_PROMPT_COUNT",len(prompts),flush=True)
raise SystemExit(code)
