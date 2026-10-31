function cocosim_lustre(cocosim, kind2, z3, model_path, mats, out)
% COCOSIM_LUSTRE  CoCoSim's Lustre for verification of a model, as its
% verification does before calling Kind2 (ToLustreVerify.run), and what
% Kind2 is to check in it. Writes out, a JSON file: the Lustre file, its
% top node, the Assertion and Proof Objective blocks and the contract
% blocks of the preprocessed model, the seconds taken, or the error.
%
%   cocosim_lustre(cocosim, kind2, z3, model_path, mats, out)
%
% cocosim is CoCoSim's folder, set up (without MATLAB) by the harness;
% kind2 and z3 the executables CoCoSim checks the Lustre's syntax with;
% mats a cell array of MAT files loaded in the base workspace first. The
% model is to be a copy in a folder of its own: CoCoSim writes next to it.
result = struct('model', model_path, 'failed', true);
try
    addpath(cocosim);
    evalc('cocosim_config');
    global KIND2 Z3 %#ok<TLEV> CoCoSim's configuration
    KIND2 = kind2;
    Z3 = z3;
    for i = 1:numel(mats)
        evalin('base', sprintf('load(''%s'')', strrep(mats{i}, '''', '''''')));
    end
    started = tic;
    [lustre, ~, failed, unsupported, abstracted, pp_model] = nasa_toLustre.ToLustre(model_path, {}, ...
        coco_nasa_utils.LusBackendType.KIND2, coco_nasa_utils.CoCoBackendType.VERIFICATION);
    result.seconds = toc(started);
    result.failed = logical(failed);
    result.lustre = lustre;
    result.unsupported = unsupported;
    result.abstracted = abstracted;
    if ~failed
        load_system(pp_model);
        [~, top, ~] = fileparts(pp_model);
        result.top = top;
        assertions = find_system(top, 'LookUnderMasks', 'all', 'BlockType', 'Assertion');
        assertions = assertions(strcmp(get_param(assertions, 'Enabled'), 'on'));
        objectives = find_system(top, 'LookUnderMasks', 'all', 'MaskType', 'Design Verifier Proof Objective');
        objectives = objectives(strcmp(get_param(objectives, 'enabled'), 'on'));
        result.assertions = [assertions; objectives];
        result.contracts = find_system(top, 'LookUnderMasks', 'all', 'MaskType', 'ContractBlock');
    end
catch e
    result.error = getReport(e, 'extended', 'hyperlinks', 'off');
end
bdclose('all');
write_json(out, result);
end
