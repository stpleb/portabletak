package gov.takx.api.plugin.annotations;
import java.lang.annotation.*;
@Target(ElementType.TYPE) @Retention(RetentionPolicy.RUNTIME)
public @interface PluginSpecification { String family(); String type(); String version(); String iconPath();
  gov.takx.api.plugin.support.SupportedPlatformVersion minimumSupportedVersion() default gov.takx.api.plugin.support.SupportedPlatformVersion.V5_0; }
