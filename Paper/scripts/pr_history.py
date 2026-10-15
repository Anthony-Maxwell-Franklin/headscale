"""Reconstruct observed PR lifecycle states without using future transitions.

Times are GitHub's observed UTC timestamps. Same-second lifecycle ties retain
API order; merge takes precedence when defining a simultaneous first disposition.
This is a recorded-event reconstruction, not a claim that deleted history is known.
"""
from bisect import bisect_left

from analyze import parse_time


def reconstruct(detail,timeline,cutoff):
    created=parse_time(detail['created_at'])
    if created>cutoff:
        raise ValueError('PR created after the pinned cutoff')
    selected=[];identifiers=set();anomalies=[];clock_differences=[]
    for position,event in enumerate(timeline):
        if event.get('event') not in ('closed','reopened','merged'):
            continue
        if not event.get('created_at'):
            anomalies.append('lifecycle_event_without_time');continue
        timestamp=parse_time(event['created_at'])
        if timestamp>cutoff:
            continue
        if timestamp<created:
            anomalies.append('lifecycle_event_before_creation');continue
        identifier=event.get('id')
        if identifier is not None:
            if identifier in identifiers:
                raise ValueError('Duplicate lifecycle event identifier')
            identifiers.add(identifier)
        selected.append(dict(time=timestamp,event=event['event'],order=position,source='timeline'))
    merged=parse_time(detail['merged_at']) if detail.get('merged_at') else None
    if (merged is None or merged>cutoff) and any(r['event']=='merged' for r in selected):
        anomalies.append('timeline_merge_without_detail_timestamp')
    if merged is not None and merged<=cutoff:
        recorded=[r['time'] for r in selected if r['event']=='merged']
        for timestamp in recorded:
            if timestamp!=merged:
                clock_differences.append(dict(event='merged',timeline_minus_detail_seconds=timestamp-merged))
            if abs(timestamp-merged)>1:
                anomalies.append('merge_timestamp_disagreement_exceeds_one_second')
        # Immutable merged_at is the common clock for prior-integration
        # exposures and merge outcomes. Some timeline notifications lag by 1s.
        for event in selected:
            if event['event']=='merged' and abs(event['time']-merged)<=1:
                event['time']=merged
        if not recorded:
            selected.append(dict(time=merged,event='merged',order=-1,source='detail'))
    selected.sort(key=lambda r:(r['time'],r['order']))
    is_open=True;has_merged=False;opened=created
    intervals=[];transitions=[];last_close=None
    for event in selected:
        timestamp=event['time'];kind=event['event']
        if kind=='reopened':
            if has_merged:
                anomalies.append('reopened_after_merge')
            if is_open:
                anomalies.append('reopened_without_observed_close')
            else:
                opened=timestamp
            is_open=True
        else:
            if kind=='merged':
                if not is_open and last_close!=timestamp:
                    anomalies.append('merge_from_closed_without_observed_reopen')
                has_merged=True
            if is_open:
                intervals.append((opened,timestamp))
            is_open=False;last_close=timestamp
        transitions.append(dict(time=timestamp,open=is_open,event=kind,source=event['source']))
    if is_open:
        intervals.append((opened,cutoff))
    terminal_events=[r for r in selected if r['event'] in ('closed','merged')]
    first=min(terminal_events,key=lambda r:(r['time'],r['event']!='merged'),default=None)
    closed=parse_time(detail['closed_at']) if detail.get('closed_at') else None
    if merged is None and closed is not None and closed<=cutoff:
        close_events=[r['time'] for r in selected if r['event']=='closed']
        if not close_events:
            anomalies.append('last_detail_close_missing_from_timeline')
        else:
            difference=max(close_events)-closed
            if difference:
                clock_differences.append(dict(event='closed',timeline_minus_detail_seconds=difference))
            # Closure event notification and detail.closed_at are separate
            # clocks. Retain their difference rather than silently replacing
            # history or treating a small notification lag as a missing event.
    updated=parse_time(detail['updated_at']) if detail.get('updated_at') else None
    if updated is not None and updated<=cutoff and 'state' in detail:
        if is_open!=(detail['state']=='open'):
            anomalies.append('state_disagreement_at_observation')
    if has_merged and is_open:
        anomalies.append('merged_pr_observed_open')
    return dict(transitions=transitions,transition_times=[r['time'] for r in transitions],
                open_intervals=intervals,open_at_cutoff=is_open,
                first_terminal_ts=first['time'] if first else None,
                first_outcome=('merge' if first['event']=='merged' else 'nonmerge_close') if first else 'censor',
                reopened_events=sum(r['event']=='reopened' for r in selected),
                same_second_lifecycle_ties=len(selected)-len({r['time'] for r in selected}),
                metadata_clock_differences=clock_differences,
                anomalies=sorted(set(anomalies)))


def pending_before(history,timestamp):
    """Use transitions strictly before submission, including neither tie nor future."""
    index=bisect_left(history['transition_times'],timestamp)-1
    return history['transitions'][index]['open'] if index>=0 else True


def in_observed_open_interval(history,timestamp):
    """Review timestamps at an interval boundary are eligible at second precision."""
    return any(start<=timestamp<=end for start,end in history['open_intervals'])
