"""Noninteractive local governance workflow: python -m Core.governance_cli --help."""
import argparse
import json
from pathlib import Path
from .assessment_serialization import read_assessment_result,write_assessment_result
from .assessment_history import atomic_write,read_history
from .governance import new_log,create_draft,transition,project,attach
from .governance_history import read_log,write_log,link_history


def _read(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('Structured governance input must be an object.')
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['draft','validate','amend','submit','approve','reject','activate',
        'revoke','supersede','expire','reopen','review','review-expired','export','attach','link-history'])
    parser.add_argument('--snapshot',required=True,help='Related immutable semantic snapshot; never overwritten.')
    parser.add_argument('--log',required=True,help='Portable append-only governance log.')
    parser.add_argument('--actor',help='Explicit recorded actor ID; approval also requires policy membership.')
    parser.add_argument('--at',required=True,help='Explicit timezone-qualified event or projection time.')
    parser.add_argument('--input',help='JSON material decision or transition input; never credentials.')
    parser.add_argument('--policy',help='Explicit actor/role authority JSON policy; default denies approval.')
    parser.add_argument('--decision-id')
    parser.add_argument('--output',help='New register or overlay output; existing files cannot be overwritten.')
    parser.add_argument('--history',help='Assessment history, only for explicit link-history metadata update.')
    args=parser.parse_args(argv)
    try:
        result=read_assessment_result(args.snapshot)
        source=Path(args.snapshot).resolve();logpath=Path(args.log).resolve()
        if source==logpath:raise ValueError('Decision log cannot overwrite the assessment snapshot.')
        if not logpath.exists() and args.operation!='draft':raise ValueError('Only draft creation can initialize a new decision log.')
        log=read_log(logpath) if logpath.exists() else new_log(result)
        if any(log.get(f)!=(result.get('identity') or {}).get(f) for f in ('AssessmentId','PrimaryEnvironmentId')):
            raise ValueError('Snapshot and governance log ownership differ.')
        material=_read(args.input) if args.input else {}
        if args.operation=='draft':
            if not args.input or not args.actor:raise ValueError('Draft requires --input and --actor.')
            updated=create_draft(log,result,material,actor=args.actor,at=args.at)
            write_log(logpath,updated,expected_hash=log['Integrity'] if logpath.exists() else None)
            print('Created draft '+updated['Events'][-1]['DecisionId'])
        elif args.operation in {'validate','review-expired','export'}:
            view=project(log,as_of=args.at)
            if args.operation=='validate':
                # Full material validation without approval or persistence.
                from .governance_contract import validate_record
                latest={event['DecisionId']:event for event in log['Events'] if event['Operation']!='review'}
                if args.decision_id and args.decision_id not in latest:raise ValueError('DecisionId was not found in the selected log.')
                for event in latest.values():
                    if not args.decision_id or event['DecisionId']==args.decision_id:
                        if event['Operation'] in {'draft','amend','submit','approve','activate','supersede'}:
                            validate_record(event['Record'],event['Context'],complete=True)
            if args.operation=='export' and not args.output:raise ValueError('Register export requires a new --output path.')
            if args.output:
                target=Path(args.output)
                if target.exists():raise ValueError('Governance export cannot overwrite an existing file.')
                atomic_write(target,view)
            report={'ValidationStatus':view['ValidationStatus'],'Summary':view['Summary']}
            if args.operation=='review-expired':report['ReviewRequirements']=[{key:r.get(key) for key in ('DecisionId','DecisionType',
                'WorkflowState','ReviewAt','ExpirationAt','ExpirationDue')} for r in view['Records'] if r['RequiresReview']]
            print(json.dumps(report,sort_keys=True))
        elif args.operation=='attach':
            if not args.output:raise ValueError('Attach requires a new --output snapshot; original run remains immutable.')
            target=Path(args.output).resolve()
            if target.exists():raise ValueError('Governance overlay cannot overwrite an existing snapshot.')
            import os
            reference=Path(os.path.relpath(logpath,target.parent)).as_posix()
            output=attach(result,log,as_of=args.at,locator=reference)
            write_assessment_result(target,output)
        elif args.operation=='link-history':
            if not args.history:raise ValueError('link-history requires --history.')
            old=read_history(args.history)
            link_history(args.history,logpath,as_of=args.at,expected_hash=old['Integrity'])
        else:
            if not args.decision_id or not args.actor:raise ValueError('Workflow transition requires --decision-id and --actor.')
            policy=_read(args.policy) if args.policy else None
            current_evidence=material.pop('CurrentEvidenceReferences',None) if args.operation=='reopen' else None
            updated=transition(log,result,args.decision_id,args.operation,actor=args.actor,at=args.at,
                policy=policy,data=material,current_evidence=current_evidence)
            write_log(logpath,updated,expected_hash=log['Integrity'])
            print('Recorded '+args.operation+' for '+args.decision_id)
    except (OSError,ValueError) as exc:
        print('Governance operation blocked: '+str(exc));return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
