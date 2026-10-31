function sldv_prove(cocosim, model_path, mats, seconds_per_property, out)
% SLDV_PROVE  Simulink Design Verifier's property proving on a model, as
% the suite's reports were made. Writes out, a JSON file: the version, the
% time limit, and for each proof objective its block, status and analysis
% time, or the error.
%
%   sldv_prove(cocosim, model_path, mats, seconds_per_property, out)
%
% cocosim is CoCoSim's folder (its library, cocosimLibs, is linked from
% the requirements of the suite's models); mats a cell array of MAT files
% loaded in the base workspace first. The analysis is given
% seconds_per_property times the number of Assertion and Proof Objective
% blocks: SLDV has a limit for the whole analysis, not per objective.
% SLDV writes in sldv_output/ of the current folder.
result = struct('model', model_path, 'failed', true);
try
    addpath(genpath(fullfile(cocosim, 'libs')));
    for i = 1:numel(mats)
        evalin('base', sprintf('load(''%s'')', strrep(mats{i}, '''', '''''')));
    end
    load_system(model_path);
    [~, model, ~] = fileparts(model_path);
    v = ver('sldv');
    result.version = [v.Version ' ' v.Release];
    assertions = find_system(model, 'LookUnderMasks', 'all', 'BlockType', 'Assertion');
    objectives = find_system(model, 'LookUnderMasks', 'all', 'MaskType', 'Design Verifier Proof Objective');
    count = max(1, numel(assertions) + numel(objectives));
    opts = sldvoptions;
    opts.Mode = 'PropertyProving';
    opts.MaxProcessTime = seconds_per_property * count;
    opts.OutputDir = fullfile(pwd, 'sldv_output', '$ModelName$');
    opts.DisplayReport = 'off';
    opts.SaveReport = 'off';
    opts.SaveHarnessModel = 'off';
    opts.SaveDataFile = 'on';
    result.limit = opts.MaxProcessTime;
    started = tic;
    [status, files] = sldvrun(model, opts, false);
    result.seconds = toc(started);
    result.status = status;
    result.failed = status ~= 1;
    if ~isempty(files) && isfield(files, 'DataFile') && ~isempty(files.DataFile)
        data = load(files.DataFile);
        data = data.sldvData;
        items = {};
        if isfield(data, 'ModelObjects')
            items = data.ModelObjects;
        end
        found = {};
        for i = 1:numel(data.Objectives)
            o = data.Objectives(i);
            entry = struct('status', o.status, 'descr', o.descr, 'type', o.type);
            if isfield(o, 'analysisTime')
                entry.time = o.analysisTime;
            end
            if isfield(o, 'modelObjectIdx') && ~isempty(items)
                entry.block = items(o.modelObjectIdx).designSid;
                try
                    entry.block = Simulink.ID.getFullName(items(o.modelObjectIdx).designSid);
                catch
                end
            end
            found{end + 1} = entry; %#ok<AGROW>
        end
        result.objectives = found;
    end
catch e
    result.error = getReport(e, 'extended', 'hyperlinks', 'off');
end
bdclose('all');
write_json(out, result);
end
